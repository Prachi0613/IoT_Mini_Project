#pragma once

#include <Arduino.h>

#include "telemetry.h"

enum class DeliveryResult {
  Delivered,
  RetryableFailure,
  Rejected,
  InvalidAcknowledgement,
  SerializationFailure,
};

bool connectWifiBounded();
DeliveryResult deliverBatch(const TelemetryBatch& batch, const String& stable_payload);
