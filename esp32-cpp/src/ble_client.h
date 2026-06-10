#pragma once
#include <Arduino.h>

// Callback fired when a buzz should be sent to the device.
// Called from the BLE/posture processing context.
// Set before calling ble_client_start().
extern void (*on_buzz_request)();

void ble_client_start();         // Spawns the BLE FreeRTOS task
void ble_client_stop();

// Fire a 500 ms vibration pulse (safe to call from any task)
void ble_buzz();

bool ble_is_connected();
