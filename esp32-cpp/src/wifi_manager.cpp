#include "wifi_manager.h"
#include "config.h"
#include "storage.h"
#include <WiFi.h>
#include <DNSServer.h>
#include <ESPmDNS.h>

static WifiMode    g_mode = WifiMode::NONE;
static String      g_ip;
static String      g_ssid;
static DNSServer*  g_dns = nullptr;
static bool        g_dns_started = false;

// ── AP / captive-portal setup ─────────────────────────────────────────────────
static void start_ap() {
    Serial.println("[WiFi] Starting setup AP: " AP_SSID);
    WiFi.mode(WIFI_AP);
    WiFi.softAPConfig(
        IPAddress(192, 168, 4, 1),
        IPAddress(192, 168, 4, 1),
        IPAddress(255, 255, 255, 0)
    );
    WiFi.softAP(AP_SSID);
    g_ip   = AP_IP_STR;
    g_ssid = AP_SSID;
    g_mode = WifiMode::AP_SETUP;

    // Captive portal DNS server will be started in wifi_manager_loop() after TCP/IP ready
    if (!g_dns) {
        g_dns = new DNSServer();
    }

    Serial.printf("[WiFi] AP up — http://%s\n", AP_IP_STR);
}

// ── STA connect ───────────────────────────────────────────────────────────────
static bool start_sta(const String& ssid, const String& pass) {
    Serial.printf("[WiFi] Connecting to '%s'...\n", ssid.c_str());
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid.c_str(), pass.c_str());

    unsigned long deadline = millis() + STA_CONNECT_TIMEOUT_MS;
    while (WiFi.status() != WL_CONNECTED && millis() < deadline)
        delay(250);

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[WiFi] STA connect failed");
        return false;
    }

    g_ip   = WiFi.localIP().toString();
    g_ssid = ssid;
    g_mode = WifiMode::STA;
    Serial.printf("[WiFi] Connected — IP: %s\n", g_ip.c_str());

    // Register mDNS
    if (MDNS.begin(MDNS_HOSTNAME)) {
        MDNS.addService("http", "tcp", WEB_PORT);
        Serial.printf("[WiFi] mDNS: http://%s.local\n", MDNS_HOSTNAME);
    }
    return true;
}

// ── Public ────────────────────────────────────────────────────────────────────
WifiMode wifi_manager_start() {
    if (storage_has_wifi()) {
        if (start_sta(storage_get_wifi_ssid(), storage_get_wifi_pass()))
            return g_mode;
        // Credentials saved but connect failed — fall through to AP
        Serial.println("[WiFi] Saved credentials failed, starting setup AP");
    }
    start_ap();
    return g_mode;
}

void wifi_manager_loop() {
    // Start DNS server on first call (after TCP/IP stack is ready)
    if (g_dns && !g_dns_started && g_mode == WifiMode::AP_SETUP) {
        g_dns->start(53, "*", IPAddress(192, 168, 4, 1));
        g_dns_started = true;
        Serial.println("[WiFi] DNS server started");
    }

    if (g_dns && g_dns_started) {
        g_dns->processNextRequest();
    }
}

WifiMode wifi_get_mode() { return g_mode; }
String   wifi_get_ip()   { return g_ip;   }
String   wifi_get_ssid() { return g_ssid; }
