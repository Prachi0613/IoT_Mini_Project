#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include "config_select.h"
#include "firmware_policy.h"

struct MotionSample {
  uint64_t sample_seq;
  int64_t t_mono_us;
  float ax_g;
  float ay_g;
  float az_g;
  float gx_dps;
  float gy_dps;
  float gz_dps;
};

struct EnvironmentSample {
  uint64_t env_seq;
  int64_t t_mono_us;
  float ambient_temperature_c;
  float ambient_humidity_pct;
  bool valid;
  const char* error_code;
};

struct QualityCounters {
  uint32_t i2c_read_errors;
  uint32_t missed_deadlines;
  uint32_t buffer_overruns;
  uint32_t dht_failures;
};

struct TelemetryBatch {
  char device_id[65];
  char boot_id[65];
  uint64_t batch_seq;
  uint16_t motion_count;
  uint8_t environment_count;
  MotionSample motion[config::MOTION_SAMPLES_PER_BATCH];
  EnvironmentSample environment[config::MAX_ENVIRONMENT_SAMPLES_PER_BATCH];
  QualityCounters quality;
};

bool serializeTelemetryBatch(const TelemetryBatch& batch, String& output);
bool validateConfiguredIdentity();
void makeBootId(char* destination, size_t capacity);
