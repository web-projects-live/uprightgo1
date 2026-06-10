#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <vector>

struct Settings {
    float  slouch_angle    = 10.0f;
    float  window_s        = 5.0f;
    float  threshold       = 0.60f;
    float  cooldown_s      = 15.0f;
    int    daily_goal_min  = 20;
    String sensitivity     = "normal"; // "lenient" | "normal" | "strict"
};

struct SessionRecord {
    String date;         // "YYYY-MM-DD"
    float  score_pct;
    float  duration_min;
    int    slouch_count;
};

bool           storage_init();
Settings       storage_load_settings();
bool           storage_save_settings(const Settings& s);

bool           storage_has_wifi();
String         storage_get_wifi_ssid();
String         storage_get_wifi_pass();
bool           storage_save_wifi(const String& ssid, const String& pass);
void           storage_clear_wifi();

std::vector<SessionRecord> storage_load_history();
bool           storage_append_session(const SessionRecord& rec);

String         storage_get_device_id(); // last 3 bytes of MAC
