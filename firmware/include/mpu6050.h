#pragma once

#include <Arduino.h>
#include <Wire.h>

#include "telemetry.h"

class Mpu6050 {
 public:
  bool begin(TwoWire& wire, uint16_t requested_hz, uint8_t dlpf_config);
  bool read(MotionSample& sample, uint64_t sample_seq, int64_t timestamp_us);
  bool available() const { return initialized_; }
  uint8_t address() const { return address_; }
  uint8_t sampleRateDivider() const { return sample_rate_divider_; }

 private:
  bool probe(uint8_t address);
  bool readRegister(uint8_t reg, uint8_t& value);
  bool writeRegister(uint8_t reg, uint8_t value);

  TwoWire* wire_ = nullptr;
  uint8_t address_ = 0;
  uint8_t sample_rate_divider_ = 0;
  bool initialized_ = false;
};
