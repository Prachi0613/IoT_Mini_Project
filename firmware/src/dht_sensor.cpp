#include "dht_sensor.h"

#include <cmath>

void AmbientDht11::begin() { dht_.begin(); }

EnvironmentSample AmbientDht11::read(uint64_t env_seq, int64_t timestamp_us) {
  const float humidity = dht_.readHumidity(false);
  const float temperature = dht_.readTemperature(false, false);
  const bool valid = std::isfinite(humidity) && std::isfinite(temperature) &&
                     humidity >= 0.0F && humidity <= 100.0F;
  return EnvironmentSample{
      .env_seq = env_seq,
      .t_mono_us = timestamp_us,
      .ambient_temperature_c = valid ? temperature : NAN,
      .ambient_humidity_pct = valid ? humidity : NAN,
      .valid = valid,
      .error_code = valid ? nullptr : "DHT11_READ_FAILED",
  };
}
