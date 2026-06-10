#include "storage.h"
#include "config.h"
#include <Preferences.h>
#include <LittleFS.h>
#include <ArduinoJson.h>
#include <WiFi.h>

static Preferences prefs;

bool storage_init() {
    if (!LittleFS.begin(true)) {
        Serial.println("[storage] LittleFS mount failed");
        return false;
    }
    return true;
}

// ── Settings ──────────────────────────────────────────────────────────────────

Settings storage_load_settings() {
    Settings s;
    prefs.begin(NVS_NAMESPACE, true);
    s.slouch_angle   = prefs.getFloat("angle",    DEFAULT_SLOUCH_ANGLE);
    s.window_s       = prefs.getFloat("window",   DEFAULT_WINDOW_S);
    s.threshold      = prefs.getFloat("thresh",   DEFAULT_THRESHOLD);
    s.cooldown_s     = prefs.getFloat("cooldown", DEFAULT_COOLDOWN_S);
    s.daily_goal_min = prefs.getInt  ("goal",     DEFAULT_DAILY_GOAL_MIN);
    s.sensitivity    = prefs.getString("sens",    "normal");
    prefs.end();
    return s;
}

bool storage_save_settings(const Settings& s) {
    prefs.begin(NVS_NAMESPACE, false);
    prefs.putFloat ("angle",    s.slouch_angle);
    prefs.putFloat ("window",   s.window_s);
    prefs.putFloat ("thresh",   s.threshold);
    prefs.putFloat ("cooldown", s.cooldown_s);
    prefs.putInt   ("goal",     s.daily_goal_min);
    prefs.putString("sens",     s.sensitivity);
    prefs.end();
    return true;
}

// ── Wi-Fi credentials ─────────────────────────────────────────────────────────

bool storage_has_wifi() {
    prefs.begin(NVS_NAMESPACE, true);
    bool has = prefs.isKey("wifi_ssid");
    prefs.end();
    return has;
}

String storage_get_wifi_ssid() {
    prefs.begin(NVS_NAMESPACE, true);
    String v = prefs.getString("wifi_ssid", "");
    prefs.end();
    return v;
}

String storage_get_wifi_pass() {
    prefs.begin(NVS_NAMESPACE, true);
    String v = prefs.getString("wifi_pass", "");
    prefs.end();
    return v;
}

bool storage_save_wifi(const String& ssid, const String& pass) {
    prefs.begin(NVS_NAMESPACE, false);
    prefs.putString("wifi_ssid", ssid);
    prefs.putString("wifi_pass", pass);
    prefs.end();
    return true;
}

void storage_clear_wifi() {
    prefs.begin(NVS_NAMESPACE, false);
    prefs.remove("wifi_ssid");
    prefs.remove("wifi_pass");
    prefs.end();
}

// ── Session history ───────────────────────────────────────────────────────────

std::vector<SessionRecord> storage_load_history() {
    std::vector<SessionRecord> records;
    File f = LittleFS.open(HISTORY_FILE, "r");
    if (!f) return records;

    JsonDocument doc;
    if (deserializeJson(doc, f)) { f.close(); return records; }
    f.close();

    for (JsonVariant v : doc.as<JsonArray>()) {
        SessionRecord r;
        r.date         = v["date"].as<String>();
        r.score_pct    = v["score"];
        r.duration_min = v["duration"];
        r.slouch_count = v["slouch_count"];
        records.push_back(r);
    }
    return records;
}

bool storage_append_session(const SessionRecord& rec) {
    auto records = storage_load_history();
    records.push_back(rec);
    // Trim to MAX_HISTORY_DAYS
    while ((int)records.size() > MAX_HISTORY_DAYS)
        records.erase(records.begin());

    JsonDocument doc;
    JsonArray arr = doc.to<JsonArray>();
    for (const auto& r : records) {
        JsonObject o = arr.add<JsonObject>();
        o["date"]        = r.date;
        o["score"]       = r.score_pct;
        o["duration"]    = r.duration_min;
        o["slouch_count"]= r.slouch_count;
    }

    File f = LittleFS.open(HISTORY_FILE, "w");
    if (!f) return false;
    serializeJson(doc, f);
    f.close();
    return true;
}

// ── Device ID ─────────────────────────────────────────────────────────────────

String storage_get_device_id() {
    String mac = WiFi.macAddress();
    // Format: "AA:BB:CC:DD:EE:FF" -> return last 3 bytes "DDEEFF"
    return mac.substring(12, 14) + mac.substring(15, 17) + mac.substring(18, 20);
}
