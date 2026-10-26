#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>

#include <new>

#include "config_select.h"
#include "dht_sensor.h"
#include "mpu6050.h"
#include "network.h"
#include "telemetry.h"

namespace {
Mpu6050 g_mpu;
AmbientDht11 g_dht(config::DHT_PIN);
QueueHandle_t g_batch_queue = nullptr;
QueueHandle_t g_environment_queue = nullptr;
portMUX_TYPE g_quality_lock = portMUX_INITIALIZER_UNLOCKED;
QualityCounters g_quality{};
char g_boot_id[65]{};
uint64_t g_batch_seq = 0;

void incrementI2cErrors(uint32_t amount = 1) {
  portENTER_CRITICAL(&g_quality_lock);
  g_quality.i2c_read_errors += amount;
  portEXIT_CRITICAL(&g_quality_lock);
}

void incrementMissedDeadlines(uint32_t amount) {
  portENTER_CRITICAL(&g_quality_lock);
  g_quality.missed_deadlines += amount;
  portEXIT_CRITICAL(&g_quality_lock);
}

void incrementBufferOverruns(uint32_t amount = 1) {
  portENTER_CRITICAL(&g_quality_lock);
  g_quality.buffer_overruns += amount;
  portEXIT_CRITICAL(&g_quality_lock);
}

void incrementDhtFailures(uint32_t amount = 1) {
  portENTER_CRITICAL(&g_quality_lock);
  g_quality.dht_failures += amount;
  portEXIT_CRITICAL(&g_quality_lock);
}

QualityCounters qualitySnapshot() {
  portENTER_CRITICAL(&g_quality_lock);
  const QualityCounters snapshot = g_quality;
  portEXIT_CRITICAL(&g_quality_lock);
  return snapshot;
}

TelemetryBatch* allocateBatch() {
  TelemetryBatch* batch = new (std::nothrow) TelemetryBatch{};
  if (batch == nullptr) {
    incrementBufferOverruns();
    Serial.println("MEMORY_ALLOCATION_FAILED: telemetry batch unavailable");
    return nullptr;
  }
  strlcpy(batch->device_id, config::DEVICE_ID, sizeof(batch->device_id));
  strlcpy(batch->boot_id, g_boot_id, sizeof(batch->boot_id));
  return batch;
}

void enqueueFinalizedBatch(TelemetryBatch*& batch) {
  if (batch == nullptr ||
      (batch->motion_count == 0 && batch->environment_count == 0)) {
    delete batch;
    batch = nullptr;
    return;
  }
  batch->batch_seq = g_batch_seq++;
  batch->quality = qualitySnapshot();
  TelemetryBatch* queued = batch;
  if (xQueueSend(g_batch_queue, &queued, 0) != pdTRUE) {
    incrementBufferOverruns();
    Serial.printf("BATCH_QUEUE_OVERFLOW: abandoned batch=%llu samples=%u\n",
                  batch->batch_seq, batch->motion_count);
    delete batch;
  }
  batch = nullptr;
}

void drainEnvironment(TelemetryBatch* batch) {
  if (batch == nullptr) return;
  EnvironmentSample sample{};
  while (batch->environment_count < config::MAX_ENVIRONMENT_SAMPLES_PER_BATCH &&
         xQueueReceive(g_environment_queue, &sample, 0) == pdTRUE) {
    batch->environment[batch->environment_count++] = sample;
  }
}

void samplingTask(void*) {
  const int64_t period_us = 1000000LL / config::REQUESTED_MPU_HZ;
  int64_t next_deadline_us = esp_timer_get_time();
  int64_t batch_started_us = next_deadline_us;
  uint64_t attempt_sequence = 0;
  TelemetryBatch* batch = allocateBatch();

  for (;;) {
    if (batch == nullptr) {
      batch = allocateBatch();
      batch_started_us = esp_timer_get_time();
      if (batch == nullptr) {
        vTaskDelay(pdMS_TO_TICKS(100));
        continue;
      }
    }
    drainEnvironment(batch);
    const int64_t now_us = esp_timer_get_time();
    const bool age_limit =
        now_us - batch_started_us >= static_cast<int64_t>(config::BATCH_MAX_AGE_MS) * 1000;

    if (!g_mpu.available()) {
      if (age_limit && batch->environment_count > 0) {
        enqueueFinalizedBatch(batch);
      }
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }

    if (now_us < next_deadline_us) {
      const int64_t remaining = next_deadline_us - now_us;
      if (remaining > 1200) {
        vTaskDelay(pdMS_TO_TICKS(1));
      } else if (remaining > 0) {
        delayMicroseconds(static_cast<uint32_t>(remaining));
      }
      continue;
    }

    const int64_t lateness = now_us - next_deadline_us;
    if (lateness >= period_us) {
      const uint32_t missed = static_cast<uint32_t>(lateness / period_us);
      incrementMissedDeadlines(missed);
      attempt_sequence += missed;  // Preserve sequence gaps; never invent samples.
      next_deadline_us += static_cast<int64_t>(missed) * period_us;
    }

    MotionSample sample{};
    const uint64_t this_attempt = attempt_sequence++;
    const int64_t acquisition_time_us = esp_timer_get_time();
    if (g_mpu.read(sample, this_attempt, acquisition_time_us)) {
      batch->motion[batch->motion_count++] = sample;
    } else {
      incrementI2cErrors();
    }
    next_deadline_us += period_us;

    if (batch->motion_count >= config::MOTION_SAMPLES_PER_BATCH || age_limit) {
      enqueueFinalizedBatch(batch);
      batch_started_us = esp_timer_get_time();
    }
  }
}

void dhtTask(void*) {
  uint64_t environment_sequence = 0;
  TickType_t previous_wake = xTaskGetTickCount();
  for (;;) {
    const int64_t timestamp_us = esp_timer_get_time();
    EnvironmentSample sample = g_dht.read(environment_sequence++, timestamp_us);
    if (!sample.valid) incrementDhtFailures();
    if (xQueueSend(g_environment_queue, &sample, 0) != pdTRUE) {
      incrementBufferOverruns();
      Serial.println("ENVIRONMENT_QUEUE_OVERFLOW: DHT observation abandoned");
    }
    vTaskDelayUntil(&previous_wake, pdMS_TO_TICKS(config::DHT_INTERVAL_MS));
  }
}

void networkTask(void*) {
  TelemetryBatch* batch = nullptr;
  for (;;) {
    if (xQueueReceive(g_batch_queue, &batch, portMAX_DELAY) != pdTRUE || batch == nullptr) {
      continue;
    }
    String stable_payload;
    if (!serializeTelemetryBatch(*batch, stable_payload)) {
      Serial.printf("SERIALIZATION_FAILED: batch=%llu\n", batch->batch_seq);
      delete batch;
      batch = nullptr;
      continue;
    }

    bool delivered = false;
    bool retryable = true;
    for (uint8_t attempt = 0;
         attempt < config::HTTP_MAX_ATTEMPTS && retryable && !delivered; ++attempt) {
      if (!connectWifiBounded()) {
        retryable = true;
      } else {
        const DeliveryResult result = deliverBatch(*batch, stable_payload);
        delivered = result == DeliveryResult::Delivered;
        retryable = result == DeliveryResult::RetryableFailure ||
                    result == DeliveryResult::InvalidAcknowledgement;
      }
      if (retryable && !delivered && attempt + 1 < config::HTTP_MAX_ATTEMPTS) {
        const uint32_t backoff = config::HTTP_BACKOFF_BASE_MS << attempt;
        vTaskDelay(pdMS_TO_TICKS(backoff));
      }
    }
    if (delivered) {
      Serial.printf("BATCH_DELIVERED: batch=%llu motion=%u environment=%u\n",
                    batch->batch_seq, batch->motion_count, batch->environment_count);
    } else {
      Serial.printf("BATCH_ABANDONED: batch=%llu after finite retry policy\n",
                    batch->batch_seq);
    }
    delete batch;
    batch = nullptr;
  }
}
}  // namespace

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("PMNode hardware firmware starting (no motor-control capability)");
  if (!validateConfiguredIdentity()) {
    Serial.println("FATAL DEVICE_ID: use 1..64 characters [A-Za-z0-9._-]");
    return;
  }
  makeBootId(g_boot_id, sizeof(g_boot_id));
  Serial.printf("device_id=%s boot_id=%s source_type=%s\n",
                config::DEVICE_ID, g_boot_id, TELEMETRY_SOURCE_TYPE);

  g_batch_queue = xQueueCreate(config::BATCH_QUEUE_LENGTH, sizeof(TelemetryBatch*));
  g_environment_queue = xQueueCreate(4, sizeof(EnvironmentSample));
  if (g_batch_queue == nullptr || g_environment_queue == nullptr) {
    Serial.println("FATAL MEMORY_ALLOCATION_FAILED: queue creation failed");
    return;
  }

  Wire.begin(config::SDA_PIN, config::SCL_PIN, 400000);
  if (!g_mpu.begin(Wire, config::REQUESTED_MPU_HZ, config::DLPF_CONFIG)) {
    incrementI2cErrors();
    Serial.println("MPU acquisition disabled; no motion samples will be fabricated");
  }
  g_dht.begin();

  TaskHandle_t network_handle = nullptr;
  TaskHandle_t dht_handle = nullptr;
  TaskHandle_t sampling_handle = nullptr;
  if (xTaskCreatePinnedToCore(networkTask, "http-transport", 8192, nullptr, 1,
                              &network_handle, 0) != pdPASS ||
      xTaskCreatePinnedToCore(dhtTask, "dht-ambient", 3072, nullptr, 1,
                              &dht_handle, 0) != pdPASS ||
      xTaskCreatePinnedToCore(samplingTask, "mpu-sampling", 4096, nullptr, 3,
                              &sampling_handle, 1) != pdPASS) {
    Serial.println("FATAL MEMORY_ALLOCATION_FAILED: task creation failed");
    if (sampling_handle != nullptr) vTaskDelete(sampling_handle);
    if (dht_handle != nullptr) vTaskDelete(dht_handle);
    if (network_handle != nullptr) vTaskDelete(network_handle);
    return;
  }
}

void loop() { vTaskDelay(pdMS_TO_TICKS(1000)); }
