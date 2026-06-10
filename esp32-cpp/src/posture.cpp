#include "posture.h"
#include "config.h"
#include <deque>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

static Settings          g_settings;   // owned copy — safe to update from any context
static PostureState      g_state;
static SemaphoreHandle_t g_mutex = nullptr;

// Sliding window: 1 = slouch sample, 0 = good sample
static std::deque<uint8_t> g_window;
static int                  g_window_cap = 1;
static int                  g_ready_thresh = 1;

// Buzz throttle
static unsigned long g_last_buzz_ms   = 0;
static bool          g_buzz_pending   = false;

// Time tracking
static unsigned long g_last_tick_ms   = 0;

static void recalc_window_params() {
    float interval_s = POLL_INTERVAL_MS / 1000.0f;
    g_window_cap   = max(3, (int)ceilf(g_settings.window_s / interval_s));
    g_ready_thresh = max(3, g_window_cap / 3);
}

void posture_init(const Settings& settings) {
    g_settings = settings;
    g_mutex    = xSemaphoreCreateMutex();
    recalc_window_params();
    g_last_tick_ms = millis();
}

void posture_update_settings(const Settings& settings) {
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    g_settings = settings;
    recalc_window_params();
    g_window.clear();
    xSemaphoreGive(g_mutex);
}

bool posture_push_angle(float angle_deg) {
    if (!g_mutex) return false;
    xSemaphoreTake(g_mutex, portMAX_DELAY);

    bool raw_slouch = (angle_deg > g_settings.slouch_angle);

    // Maintain window
    g_window.push_back(raw_slouch ? 1 : 0);
    while ((int)g_window.size() > g_window_cap)
        g_window.pop_front();

    g_state.angle = angle_deg;
    bool fired = false;

    if (g_state.mode == "desk" && (int)g_window.size() >= g_ready_thresh) {
        int sum = 0;
        for (uint8_t v : g_window) sum += v;
        g_state.slouch_ratio = (float)sum / g_window.size();
        g_state.is_slouching = g_state.slouch_ratio >= g_settings.threshold;

        unsigned long now = millis();
        if (g_state.is_slouching && !g_buzz_pending) {
            float elapsed_s = (now - g_last_buzz_ms) / 1000.0f;
            if (g_last_buzz_ms == 0 || elapsed_s >= g_settings.cooldown_s) {
                g_buzz_pending = true;
                g_state.slouch_count++;
                fired = true;
            }
        }
    } else {
        g_state.slouch_ratio = 0.0f;
        g_state.is_slouching = false;
    }

    xSemaphoreGive(g_mutex);
    return fired;
}

bool posture_tick() {
    if (!g_mutex) return false;
    xSemaphoreTake(g_mutex, portMAX_DELAY);

    unsigned long now = millis();
    float dt = (now - g_last_tick_ms) / 1000.0f;
    g_last_tick_ms = now;

    if (g_state.ble_connected && g_state.mode == "desk") {
        g_state.total_seconds += dt;
        if (!g_state.is_slouching)
            g_state.good_seconds += dt;
    }

    bool fire = g_buzz_pending;
    if (g_buzz_pending) {
        g_buzz_pending  = false;
        g_last_buzz_ms  = now;
    }

    xSemaphoreGive(g_mutex);
    return fire;
}

PostureState posture_get_state() {
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    PostureState s = g_state;
    xSemaphoreGive(g_mutex);
    return s;
}

void posture_set_mode(const String& mode) {
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    g_state.mode = mode;
    if (mode == "break") {
        g_window.clear();
        g_state.is_slouching = false;
        g_state.slouch_ratio = 0.0f;
    }
    xSemaphoreGive(g_mutex);
}

void posture_reset_session() {
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    g_state.slouch_count  = 0;
    g_state.good_seconds  = 0.0f;
    g_state.total_seconds = 0.0f;
    g_window.clear();
    g_last_buzz_ms = 0;
    xSemaphoreGive(g_mutex);
}

void posture_set_ble_connected(bool connected) {
    xSemaphoreTake(g_mutex, portMAX_DELAY);
    g_state.ble_connected = connected;
    if (!connected) {
        g_state.is_slouching = false;
        g_state.slouch_ratio = 0.0f;
        g_window.clear();
    }
    xSemaphoreGive(g_mutex);
}
