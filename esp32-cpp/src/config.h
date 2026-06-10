#pragma once

// ── BLE ──────────────────────────────────────────────────────────────────────
#define BLE_DEVICE_NAME        "UprightGO"
#define CHAR_UUID_ANGLE        "0000aaca-0000-1000-8000-00805f9b34fb"
#define CHAR_UUID_VIBRATE      "0000aad3-0000-1000-8000-00805f9b34fb"
#define BLE_SCAN_DURATION_S    5      // seconds per scan attempt
#define BLE_RECONNECT_DELAY_MS 5000   // ms between reconnect attempts
#define POLL_INTERVAL_MS       150    // ms between angle reads

// ── Wi-Fi ─────────────────────────────────────────────────────────────────────
#define AP_SSID                "UprightGO-Setup"
#define AP_IP_STR              "192.168.4.1"
#define STA_CONNECT_TIMEOUT_MS 15000  // ms to wait for STA connect
#define MDNS_HOSTNAME          "uprightgo"   // → http://uprightgo.local

// ── Web server ────────────────────────────────────────────────────────────────
#define WEB_PORT               80

// ── Storage (NVS namespace) ───────────────────────────────────────────────────
#define NVS_NAMESPACE          "upright"
#define HISTORY_FILE           "/history.json"
#define MAX_HISTORY_DAYS       30

// ── Posture defaults ──────────────────────────────────────────────────────────
#define DEFAULT_SLOUCH_ANGLE   10.0f  // degrees above baseline = slouch
#define DEFAULT_WINDOW_S       5.0f   // sliding-window width in seconds
#define DEFAULT_THRESHOLD      0.60f  // fraction of window that must be slouch
#define DEFAULT_COOLDOWN_S     15.0f  // seconds between buzz alerts
#define DEFAULT_DAILY_GOAL_MIN 20     // minutes per day target

// ── Vibration pulse ───────────────────────────────────────────────────────────
#define BUZZ_DURATION_MS       500    // ms to hold the vibration motor on
