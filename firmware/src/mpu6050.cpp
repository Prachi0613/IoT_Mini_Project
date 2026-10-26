#include "mpu6050.h"

#include "firmware_policy.h"

namespace {
constexpr uint8_t ADDRESS_LOW = 0x68;
constexpr uint8_t ADDRESS_HIGH = 0x69;
constexpr uint8_t REG_SMPLRT_DIV = 0x19;
constexpr uint8_t REG_CONFIG = 0x1A;
constexpr uint8_t REG_GYRO_CONFIG = 0x1B;
constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
constexpr uint8_t REG_PWR_MGMT_1 = 0x6B;
constexpr uint8_t REG_WHO_AM_I = 0x75;

int16_t signedWord(uint8_t high, uint8_t low) {
  return static_cast<int16_t>((static_cast<uint16_t>(high) << 8U) | low);
}
}  // namespace

bool Mpu6050::begin(TwoWire& wire, uint16_t requested_hz, uint8_t dlpf_config) {
  wire_ = &wire;
  initialized_ = false;
  if (dlpf_config < 1 || dlpf_config > 6 || requested_hz == 0 ||
      requested_hz > 1000 || (1000 % requested_hz) != 0) {
    Serial.println("FATAL MPU_CONFIG: DLPF 1..6 requires an exact divisor of 1000 Hz");
    return false;
  }
  sample_rate_divider_ = static_cast<uint8_t>((1000 / requested_hz) - 1);
  if (probe(ADDRESS_LOW)) {
    address_ = ADDRESS_LOW;
  } else if (probe(ADDRESS_HIGH)) {
    address_ = ADDRESS_HIGH;
  } else {
    Serial.println("FATAL MPU_NOT_DETECTED: checked I2C addresses 0x68 and 0x69");
    return false;
  }

  uint8_t identity = 0;
  if (!readRegister(REG_WHO_AM_I, identity) ||
      (identity != ADDRESS_LOW && identity != ADDRESS_HIGH)) {
    Serial.printf("FATAL MPU_IDENTITY: WHO_AM_I=0x%02X\n", identity);
    return false;
  }
  if (!writeRegister(REG_PWR_MGMT_1, 0x01)) {  // Wake, PLL with X gyro.
    Serial.println("FATAL MPU_WAKE_FAILED");
    return false;
  }
  delay(100);
  if (!writeRegister(REG_CONFIG, dlpf_config & 0x07) ||
      !writeRegister(REG_GYRO_CONFIG, 0x00) ||     // +/-250 dps, 131 LSB/dps.
      !writeRegister(REG_ACCEL_CONFIG, 0x08) ||   // +/-4 g, 8192 LSB/g.
      !writeRegister(REG_SMPLRT_DIV, sample_rate_divider_)) {
    Serial.println("FATAL MPU_CONFIGURATION_WRITE_FAILED");
    return false;
  }
  initialized_ = true;
  Serial.printf("MPU6050 ready address=0x%02X requested_hz=%u divider=%u dlpf=%u\n",
                address_, requested_hz, sample_rate_divider_, dlpf_config);
  return true;
}

bool Mpu6050::probe(uint8_t address) {
  wire_->beginTransmission(address);
  return wire_->endTransmission(true) == 0;
}

bool Mpu6050::readRegister(uint8_t reg, uint8_t& value) {
  wire_->beginTransmission(address_);
  wire_->write(reg);
  if (wire_->endTransmission(false) != 0 || wire_->requestFrom(address_, uint8_t{1}, true) != 1) {
    return false;
  }
  value = wire_->read();
  return true;
}

bool Mpu6050::writeRegister(uint8_t reg, uint8_t value) {
  wire_->beginTransmission(address_);
  wire_->write(reg);
  wire_->write(value);
  return wire_->endTransmission(true) == 0;
}

bool Mpu6050::read(MotionSample& sample, uint64_t sample_seq, int64_t timestamp_us) {
  if (!initialized_) return false;
  wire_->beginTransmission(address_);
  wire_->write(REG_ACCEL_XOUT_H);
  if (wire_->endTransmission(false) != 0 ||
      wire_->requestFrom(address_, uint8_t{14}, true) != 14) {
    while (wire_->available()) wire_->read();
    return false;
  }
  uint8_t bytes[14];
  for (uint8_t& value : bytes) value = wire_->read();
  sample.sample_seq = sample_seq;
  sample.t_mono_us = timestamp_us;
  sample.ax_g = signedWord(bytes[0], bytes[1]) / MPU6050_ACCEL_LSB_PER_G_4G;
  sample.ay_g = signedWord(bytes[2], bytes[3]) / MPU6050_ACCEL_LSB_PER_G_4G;
  sample.az_g = signedWord(bytes[4], bytes[5]) / MPU6050_ACCEL_LSB_PER_G_4G;
  // bytes 6..7 are MPU die temperature and are deliberately not transmitted.
  sample.gx_dps = signedWord(bytes[8], bytes[9]) / MPU6050_GYRO_LSB_PER_DPS_250;
  sample.gy_dps = signedWord(bytes[10], bytes[11]) / MPU6050_GYRO_LSB_PER_DPS_250;
  sample.gz_dps = signedWord(bytes[12], bytes[13]) / MPU6050_GYRO_LSB_PER_DPS_250;
  return true;
}
