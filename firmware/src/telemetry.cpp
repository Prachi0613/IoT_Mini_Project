#include "telemetry.h"

#include <WiFi.h>
#include <esp_system.h>

#include <cmath>

namespace {
bool validIdentifier(const char* value) {
  const size_t length = strlen(value);
  if (length == 0 || length > 64) return false;
  for (size_t index = 0; index < length; ++index) {
    const char c = value[index];
    if (!(isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '_' || c == '-')) {
      return false;
    }
  }
  return true;
}

void addMotion(JsonArray array, const MotionSample& sample) {
  JsonObject item = array.add<JsonObject>();
  item["sample_seq"] = sample.sample_seq;
  item["t_mono_us"] = sample.t_mono_us;
  item["ax_g"] = sample.ax_g;
  item["ay_g"] = sample.ay_g;
  item["az_g"] = sample.az_g;
  item["gx_dps"] = sample.gx_dps;
  item["gy_dps"] = sample.gy_dps;
  item["gz_dps"] = sample.gz_dps;
}

void addEnvironment(JsonArray array, const EnvironmentSample& sample) {
  JsonObject item = array.add<JsonObject>();
  item["env_seq"] = sample.env_seq;
  item["t_mono_us"] = sample.t_mono_us;
  if (sample.valid) {
    item["ambient_temperature_c"] = sample.ambient_temperature_c;
    item["ambient_humidity_pct"] = sample.ambient_humidity_pct;
    item["valid"] = true;
    item["error_code"] = nullptr;
  } else {
    item["ambient_temperature_c"] = nullptr;
    item["ambient_humidity_pct"] = nullptr;
    item["valid"] = false;
    item["error_code"] = sample.error_code;
  }
}
}  // namespace

bool validateConfiguredIdentity() { return validIdentifier(config::DEVICE_ID); }

void makeBootId(char* destination, size_t capacity) {
  const uint32_t a = esp_random();
  const uint32_t b = esp_random();
  const uint32_t c = esp_random();
  const uint32_t d = esp_random();
  snprintf(destination, capacity, "boot-%08lx%08lx%08lx%08lx",
           static_cast<unsigned long>(a), static_cast<unsigned long>(b),
           static_cast<unsigned long>(c), static_cast<unsigned long>(d));
}

bool serializeTelemetryBatch(const TelemetryBatch& batch, String& output) {
  JsonDocument document;
  document["schema_version"] = TELEMETRY_SCHEMA_VERSION;
  document["device_id"] = batch.device_id;
  document["boot_id"] = batch.boot_id;
  document["batch_seq"] = batch.batch_seq;
  document["source_type"] = TELEMETRY_SOURCE_TYPE;
  document["requested_mpu_hz"] = config::REQUESTED_MPU_HZ;
  document["accel_range_g"] = config::ACCEL_RANGE_G;
  document["gyro_range_dps"] = config::GYRO_RANGE_DPS;
  document["dlpf_config"] = config::DLPF_CONFIG;
  JsonArray motion = document["motion_samples"].to<JsonArray>();
  for (uint16_t index = 0; index < batch.motion_count; ++index) {
    addMotion(motion, batch.motion[index]);
  }
  JsonArray environment = document["environment_samples"].to<JsonArray>();
  for (uint8_t index = 0; index < batch.environment_count; ++index) {
    addEnvironment(environment, batch.environment[index]);
  }
  JsonObject quality = document["quality"].to<JsonObject>();
  quality["i2c_read_errors"] = batch.quality.i2c_read_errors;
  quality["missed_deadlines"] = batch.quality.missed_deadlines;
  quality["buffer_overruns"] = batch.quality.buffer_overruns;
  quality["dht_failures"] = batch.quality.dht_failures;
  if (document.overflowed()) return false;
  output = "";
  output.reserve(measureJson(document) + 1);
  const size_t written = serializeJson(document, output);
  return written > 0 && written <= BACKEND_MAX_REQUEST_BYTES;
}
