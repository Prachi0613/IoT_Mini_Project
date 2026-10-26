#pragma once

// Copy to config.h and change values locally. config.h is Git-ignored.
// Never use localhost: on an ESP32 it refers to the ESP32, not the laptop.
namespace config {
inline constexpr char WIFI_SSID[] = "CHANGE_ME";
inline constexpr char WIFI_PASSWORD[] = "CHANGE_ME";
inline constexpr char BACKEND_HOST[] = "192.168.1.2";
inline constexpr uint16_t BACKEND_PORT = 8000;
inline constexpr char DEVICE_ID[] = "esp32-pmnode-01";

inline constexpr uint8_t SDA_PIN = 21;
inline constexpr uint8_t SCL_PIN = 22;
inline constexpr uint8_t DHT_PIN = 27;
inline constexpr uint16_t REQUESTED_MPU_HZ = 500;
// DLPF 1: nominal 184 Hz accelerometer / 188 Hz gyro bandwidth. This keeps the
// planned 30-100 Hz feature band inside the nominal sensor passband; actual
// response, noise, and alias rejection still require physical validation.
inline constexpr uint8_t DLPF_CONFIG = 1;
inline constexpr float ACCEL_RANGE_G = 4.0F;
inline constexpr float GYRO_RANGE_DPS = 250.0F;

inline constexpr size_t MOTION_SAMPLES_PER_BATCH = 100;
inline constexpr size_t MAX_ENVIRONMENT_SAMPLES_PER_BATCH = 2;
inline constexpr size_t BATCH_QUEUE_LENGTH = 4;
inline constexpr uint32_t BATCH_MAX_AGE_MS = 250;
inline constexpr uint32_t DHT_INTERVAL_MS = 2000;
inline constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 15000;
inline constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 5000;
inline constexpr uint32_t HTTP_CONNECT_TIMEOUT_MS = 3000;
inline constexpr uint32_t HTTP_RESPONSE_TIMEOUT_MS = 5000;
inline constexpr uint8_t HTTP_MAX_ATTEMPTS = 3;
inline constexpr uint32_t HTTP_BACKOFF_BASE_MS = 500;
inline constexpr uint8_t DIAGNOSTIC_LOG_LEVEL = 1;
}  // namespace config
