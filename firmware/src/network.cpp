#include "network.h"

#include <HTTPClient.h>
#include <WiFi.h>

#include "config_select.h"

namespace {
String endpointUrl() {
  return "http://" + String(config::BACKEND_HOST) + ":" +
         String(config::BACKEND_PORT) + "/api/v1/batches";
}

bool acknowledgementMatches(const String& body, const TelemetryBatch& batch) {
  JsonDocument response;
  if (deserializeJson(response, body) != DeserializationError::Ok) return false;
  if (!response["device_id"].is<const char*>() ||
      !response["boot_id"].is<const char*>() ||
      !response["batch_seq"].is<uint64_t>() ||
      !response["duplicate"].is<bool>() ||
      !response["accepted_motion_samples"].is<uint16_t>() ||
      !response["accepted_environment_samples"].is<uint8_t>()) {
    return false;
  }
  if (response["device_id"].as<String>() != batch.device_id ||
      response["boot_id"].as<String>() != batch.boot_id ||
      response["batch_seq"].as<uint64_t>() != batch.batch_seq) {
    return false;
  }
  const bool duplicate = response["duplicate"].as<bool>();
  const uint16_t accepted_motion = response["accepted_motion_samples"].as<uint16_t>();
  const uint8_t accepted_environment = response["accepted_environment_samples"].as<uint8_t>();
  return duplicate ? (accepted_motion == 0 && accepted_environment == 0)
                   : (accepted_motion == batch.motion_count &&
                      accepted_environment == batch.environment_count);
}
}  // namespace

bool validateNetworkConfiguration() {
  const String host(config::BACKEND_HOST);
  if (host.isEmpty() || host.equalsIgnoreCase("localhost") ||
      host == "127.0.0.1" || host == "0.0.0.0") {
    Serial.println("FATAL BACKEND_CONFIG: use the laptop LAN IPv4 address, not localhost");
    return false;
  }
  if (config::BACKEND_PORT == 0) {
    Serial.println("FATAL BACKEND_CONFIG: backend port must be non-zero");
    return false;
  }
  return true;
}

bool connectWifiBounded() {
  if (strcmp(config::WIFI_SSID, "CHANGE_ME") == 0) {
    Serial.println("FATAL WIFI_CONFIG: create include/config.h with local credentials");
    return false;
  }
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(config::WIFI_SSID, config::WIFI_PASSWORD);
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - started < config::WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
  }
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.disconnect(false, false);
    Serial.println("WIFI_UNAVAILABLE: bounded connection attempt expired");
    return false;
  }
  Serial.print("Wi-Fi connected, device IP: ");
  Serial.println(WiFi.localIP());
  return true;
}

DeliveryResult deliverBatch(const TelemetryBatch& batch, const String& stable_payload) {
  if (WiFi.status() != WL_CONNECTED) return DeliveryResult::RetryableFailure;
  HTTPClient http;
  http.setConnectTimeout(config::HTTP_CONNECT_TIMEOUT_MS);
  http.setTimeout(config::HTTP_RESPONSE_TIMEOUT_MS);
  http.setReuse(false);
  if (!http.begin(endpointUrl())) return DeliveryResult::RetryableFailure;
  http.addHeader("Content-Type", "application/json");
  const int status = http.POST(reinterpret_cast<const uint8_t*>(stable_payload.c_str()),
                               stable_payload.length());
  const String response_body = status > 0 ? http.getString() : String{};
  http.end();
  if (status == 201) {
    return acknowledgementMatches(response_body, batch)
               ? DeliveryResult::Delivered
               : DeliveryResult::InvalidAcknowledgement;
  }
  if (status == 409 || status == 413 || status == 422) {
    Serial.printf("TELEMETRY_REJECTED: HTTP %d batch=%llu\n", status, batch.batch_seq);
    return DeliveryResult::Rejected;
  }
  Serial.printf("HTTP_UNAVAILABLE: status=%d batch=%llu\n", status, batch.batch_seq);
  return DeliveryResult::RetryableFailure;
}
