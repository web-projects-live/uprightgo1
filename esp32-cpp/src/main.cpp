#include <Arduino.h>
#include "config.h"
#include "storage.h"
#include "wifi_manager.h"
#include "ble_client.h"
#include "posture.h"
#include "web_server.h"

static Settings g_settings;

static unsigned long g_last_push_ms = 0;
static const unsigned long PUSH_INTERVAL_MS = 250;

static bool g_prev_connected = false;
static bool g_prev_slouching = false;

static bool g_web_server_started = false;

void setup() {
    Serial.begin(115200);
    Serial.println("\n[main] Upright Go C++ firmware starting");

    storage_init();
    g_settings = storage_load_settings();

    wifi_manager_start();
    posture_init(g_settings);
    ble_client_start();

    Serial.println("[main] Setup complete");
}

void loop() {
    // Deferred web server startup (wait for WiFi TCP/IP stack to be ready)
    if (!g_web_server_started && millis() > 2000) {
        web_server_start();
        g_web_server_started = true;
        Serial.println("[main] Web server started");
    }

    wifi_manager_loop();
    posture_tick();

    unsigned long now = millis();
    PostureState state = posture_get_state();

    bool state_changed = (state.ble_connected != g_prev_connected ||
                          state.is_slouching   != g_prev_slouching);

    if (state_changed || (now - g_last_push_ms >= PUSH_INTERVAL_MS)) {
        web_server_push_state();
        g_last_push_ms   = now;
        g_prev_connected = state.ble_connected;
        g_prev_slouching = state.is_slouching;
    }

    delay(50);
}
