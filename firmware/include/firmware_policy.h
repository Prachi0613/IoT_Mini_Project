#pragma once

#include <Arduino.h>

inline constexpr char TELEMETRY_SCHEMA_VERSION[] = "1.0.0";
inline constexpr char TELEMETRY_SOURCE_TYPE[] = "HARDWARE";
inline constexpr size_t BACKEND_MAX_MOTION_SAMPLES = 1000;
inline constexpr size_t BACKEND_MAX_ENVIRONMENT_SAMPLES = 20;
inline constexpr size_t BACKEND_MAX_REQUEST_BYTES = 2000000;
inline constexpr float MPU6050_ACCEL_LSB_PER_G_4G = 8192.0F;
inline constexpr float MPU6050_GYRO_LSB_PER_DPS_250 = 131.0F;

static_assert(config::MOTION_SAMPLES_PER_BATCH > 0);
static_assert(config::MOTION_SAMPLES_PER_BATCH <= BACKEND_MAX_MOTION_SAMPLES);
static_assert(config::MAX_ENVIRONMENT_SAMPLES_PER_BATCH <=
              BACKEND_MAX_ENVIRONMENT_SAMPLES);
static_assert(config::BATCH_QUEUE_LENGTH > 0);
static_assert(config::HTTP_MAX_ATTEMPTS > 0);
