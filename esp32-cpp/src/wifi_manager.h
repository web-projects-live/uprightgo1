#pragma once
#include <Arduino.h>

enum class WifiMode { NONE, AP_SETUP, STA };

// Blocking: starts AP or connects to STA. Returns when ready.
WifiMode wifi_manager_start();

WifiMode  wifi_get_mode();
String    wifi_get_ip();
String    wifi_get_ssid();

// Call from loop() when in AP mode — drives the captive DNS server.
void wifi_manager_loop();
