#pragma once
#include <Arduino.h>
#include "storage.h"

struct PostureState {
    bool   ble_connected  = false;
    float  angle          = 0.0f;
    bool   is_slouching   = false;
    float  slouch_ratio   = 0.0f;   // 0.0–1.0 for the pressure bar
    uint32_t slouch_count = 0;
    float  good_seconds   = 0.0f;
    float  total_seconds  = 0.0f;
    String mode           = "desk"; // "desk" | "moving" | "break"
    bool   session_active = false;
    unsigned long session_start_ms = 0;
};

// Must be called before any push_angle()
void posture_init(const Settings& settings);
void posture_update_settings(const Settings& settings);

// Called from BLE notification callback (may be on BLE task).
// Returns true if a buzz should fire (caller drives vibration).
bool posture_push_angle(float angle_deg);

// Call periodically (e.g. every 100 ms) to update time counters.
// Returns true if a pending buzz needs to fire.
bool posture_tick();

PostureState posture_get_state();
void posture_set_mode(const String& mode);
void posture_reset_session();
void posture_set_ble_connected(bool connected);
