#pragma once

#include <DHT.h>

#include "telemetry.h"

class AmbientDht11 {
 public:
  explicit AmbientDht11(uint8_t pin) : dht_(pin, DHT11) {}
  void begin();
  EnvironmentSample read(uint64_t env_seq, int64_t timestamp_us);

 private:
  DHT dht_;
};
