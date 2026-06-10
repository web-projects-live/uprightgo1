#include "web_server.h"
#include "config.h"
#include "posture.h"
#include "storage.h"
#include "wifi_manager.h"
#include <ESPAsyncWebServer.h>
#include <LittleFS.h>
#include <ArduinoJson.h>

static AsyncWebServer   server(WEB_PORT);
static AsyncEventSource events("/api/stream");

// ── Helpers ───────────────────────────────────────────────────────────────────

static String state_to_json() {
    PostureState s = posture_get_state();
    JsonDocument doc;
    doc["connected"]     = s.ble_connected;
    doc["angle"]         = s.angle;
    doc["is_slouching"]  = s.is_slouching;
    doc["slouch_ratio"]  = s.slouch_ratio;
    doc["slouch_count"]  = s.slouch_count;
    doc["good_seconds"]  = s.good_seconds;
    doc["total_seconds"] = s.total_seconds;
    doc["mode"]          = s.mode;

    if (s.total_seconds > 0)
        doc["score"] = (s.good_seconds / s.total_seconds) * 100.0f;
    else
        doc["score"] = 0;

    // Wi-Fi info
    doc["wifi_connected"] = (wifi_get_mode() == WifiMode::STA);
    doc["wifi_ssid"]      = wifi_get_ssid();
    doc["ip"]             = wifi_get_ip();

    String out;
    serializeJson(doc, out);
    return out;
}

static String settings_to_json(const Settings& s) {
    JsonDocument doc;
    doc["slouch_angle"]   = s.slouch_angle;
    doc["window_s"]       = s.window_s;
    doc["threshold"]      = s.threshold;
    doc["cooldown_s"]     = s.cooldown_s;
    doc["daily_goal_min"] = s.daily_goal_min;
    doc["sensitivity"]    = s.sensitivity;
    String out;
    serializeJson(doc, out);
    return out;
}

static Settings apply_preset(const String& preset) {
    Settings s;
    s.sensitivity = preset;
    if (preset == "lenient") {
        s.window_s   = 8.0f; s.threshold = 0.70f; s.cooldown_s = 30.0f;
    } else if (preset == "strict") {
        s.window_s   = 2.5f; s.threshold = 0.50f; s.cooldown_s = 8.0f;
    } else { // normal
        s.window_s   = 5.0f; s.threshold = 0.60f; s.cooldown_s = 15.0f;
    }
    return s;
}

// ── Captive portal redirect (AP mode) ─────────────────────────────────────────
static void add_captive_redirect(AsyncWebServer& srv) {
    srv.onNotFound([](AsyncWebServerRequest* req) {
        if (wifi_get_mode() == WifiMode::AP_SETUP) {
            req->redirect("http://" AP_IP_STR "/setup");
        } else {
            req->send(404, "text/plain", "Not found");
        }
    });
}

// ── Routes ────────────────────────────────────────────────────────────────────
void web_server_start() {

    // ── Static files from LittleFS ──────────────────────────────────────────
    server.serveStatic("/", LittleFS, "/").setDefaultFile("index.html");

    // ── SSE stream ──────────────────────────────────────────────────────────
    events.onConnect([](AsyncEventSourceClient* client) {
        // Send current state immediately on connect
        client->send(state_to_json().c_str(), "state", millis(), 1000);
    });
    server.addHandler(&events);

    // ── GET /api/status ─────────────────────────────────────────────────────
    server.on("/api/status", HTTP_GET, [](AsyncWebServerRequest* req) {
        req->send(200, "application/json", state_to_json());
    });

    // ── GET /api/settings ───────────────────────────────────────────────────
    server.on("/api/settings", HTTP_GET, [](AsyncWebServerRequest* req) {
        Settings s = storage_load_settings();
        req->send(200, "application/json", settings_to_json(s));
    });

    // ── POST /api/settings ──────────────────────────────────────────────────
    server.on("/api/settings", HTTP_POST,
        [](AsyncWebServerRequest* req) {},
        nullptr,
        [](AsyncWebServerRequest* req, uint8_t* data, size_t len, size_t, size_t) {
            JsonDocument doc;
            if (deserializeJson(doc, data, len)) {
                req->send(400, "application/json", "{\"error\":\"bad json\"}");
                return;
            }
            Settings s = storage_load_settings();

            // Sensitivity preset overrides individual values
            if (doc["sensitivity"].is<const char*>()) {
                String preset = doc["sensitivity"].as<String>();
                if (preset != "custom") s = apply_preset(preset);
                s.sensitivity = preset;
            }
            if (doc["slouch_angle"].is<float>())
                s.slouch_angle   = constrain((float)doc["slouch_angle"],   1.0f, 45.0f);
            if (doc["window_s"].is<float>())
                s.window_s       = constrain((float)doc["window_s"],       1.0f, 15.0f);
            if (doc["threshold"].is<float>())
                s.threshold      = constrain((float)doc["threshold"],      0.1f,  0.9f);
            if (doc["cooldown_s"].is<float>())
                s.cooldown_s     = constrain((float)doc["cooldown_s"],     3.0f, 120.0f);
            if (doc["daily_goal_min"].is<int>())
                s.daily_goal_min = constrain((int)doc["daily_goal_min"],   5,     60);

            storage_save_settings(s);
            posture_update_settings(s);
            req->send(200, "application/json", settings_to_json(s));
        }
    );

    // ── POST /api/mode ───────────────────────────────────────────────────────
    server.on("/api/mode", HTTP_POST,
        [](AsyncWebServerRequest* req) {},
        nullptr,
        [](AsyncWebServerRequest* req, uint8_t* data, size_t len, size_t, size_t) {
            JsonDocument doc;
            if (deserializeJson(doc, data, len)) {
                req->send(400, "application/json", "{\"error\":\"bad json\"}");
                return;
            }
            String mode = doc["mode"] | "desk";
            if (mode != "desk" && mode != "moving" && mode != "break")
                mode = "desk";
            posture_set_mode(mode);
            req->send(200, "application/json", "{\"ok\":true}");
            web_server_push_state();
        }
    );

    // ── POST /api/reset ──────────────────────────────────────────────────────
    server.on("/api/reset", HTTP_POST, [](AsyncWebServerRequest* req) {
        PostureState old_state = posture_get_state();
        // Save session if meaningful
        if (old_state.total_seconds > 30) {
            SessionRecord rec;
            // Use current time — if no NTP, use uptime as placeholder
            rec.date         = "today"; // TODO: RTC/NTP for real dates
            rec.duration_min = old_state.total_seconds / 60.0f;
            rec.score_pct    = old_state.total_seconds > 0
                ? (old_state.good_seconds / old_state.total_seconds) * 100.0f : 0;
            rec.slouch_count = old_state.slouch_count;
            storage_append_session(rec);
        }
        posture_reset_session();
        req->send(200, "application/json", "{\"ok\":true}");
        web_server_push_state();
    });

    // ── GET /api/history ─────────────────────────────────────────────────────
    server.on("/api/history", HTTP_GET, [](AsyncWebServerRequest* req) {
        auto records = storage_load_history();
        JsonDocument doc;
        JsonArray arr = doc.to<JsonArray>();
        for (const auto& r : records) {
            JsonObject o = arr.add<JsonObject>();
            o["date"]        = r.date;
            o["score"]       = r.score_pct;
            o["duration"]    = r.duration_min;
            o["slouch_count"]= r.slouch_count;
        }
        String out;
        serializeJson(doc, out);
        req->send(200, "application/json", out);
    });

    // ── GET /api/wifi ─────────────────────────────────────────────────────────
    server.on("/api/wifi", HTTP_GET, [](AsyncWebServerRequest* req) {
        JsonDocument doc;
        doc["mode"]       = wifi_get_mode() == WifiMode::STA ? "sta" : "ap";
        doc["ssid"]       = wifi_get_ssid();
        doc["ip"]         = wifi_get_ip();
        doc["mdns"]       = String(MDNS_HOSTNAME) + ".local";
        String out;
        serializeJson(doc, out);
        req->send(200, "application/json", out);
    });

    // ── POST /api/wifi ────────────────────────────────────────────────────────
    // Save new credentials and reboot into STA mode.
    server.on("/api/wifi", HTTP_POST,
        [](AsyncWebServerRequest* req) {},
        nullptr,
        [](AsyncWebServerRequest* req, uint8_t* data, size_t len, size_t, size_t) {
            JsonDocument doc;
            if (deserializeJson(doc, data, len)) {
                req->send(400, "application/json", "{\"error\":\"bad json\"}");
                return;
            }
            String ssid = doc["ssid"] | "";
            String pass = doc["pass"] | "";
            if (ssid.isEmpty()) {
                req->send(400, "application/json", "{\"error\":\"ssid required\"}");
                return;
            }
            storage_save_wifi(ssid, pass);
            // Respond before rebooting
            req->send(200, "application/json", "{\"ok\":true,\"rebooting\":true}");
            delay(500);
            ESP.restart();
        }
    );

    // ── DELETE /api/wifi (forget credentials) ─────────────────────────────────
    server.on("/api/wifi", HTTP_DELETE, [](AsyncWebServerRequest* req) {
        storage_clear_wifi();
        req->send(200, "application/json", "{\"ok\":true,\"rebooting\":true}");
        delay(500);
        ESP.restart();
    });

    // ── Captive portal catch-all ──────────────────────────────────────────────
    add_captive_redirect(server);

    server.begin();
    Serial.printf("[Web] Server started on port %d\n", WEB_PORT);
}

void web_server_push_state() {
    if (events.count() > 0)
        events.send(state_to_json().c_str(), "state", millis());
}
