#include "ble_client.h"
#include "config.h"
#include "posture.h"
#include <BLEDevice.h>
#include <BLEClient.h>
#include <BLEUtils.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

void (*on_buzz_request)() = nullptr;

static BLEClient* g_client = nullptr;
static BLERemoteCharacteristic* g_angle_char = nullptr;
static BLERemoteCharacteristic* g_vibrate_char = nullptr;
static volatile bool g_connected = false;
static volatile bool g_buzz_queued = false;
static SemaphoreHandle_t g_buzz_sem = nullptr;
static BLEAdvertisedDevice* g_target_device = nullptr;
static SemaphoreHandle_t g_connect_sem = nullptr;

// Notification callback for angle characteristic
static void angle_notify_cb(BLERemoteCharacteristic* pBLERemoteCharacteristic,
                             uint8_t* pData, size_t length, bool isNotify) {
    if (length < 2) return;
    int16_t raw = (int16_t)((pData[0] << 8) | pData[1]);
    float angle = raw / 100.0f;
    if (posture_push_angle(angle)) {
        g_buzz_queued = true;
        xSemaphoreGive(g_buzz_sem);
    }
}

class ClientCallback : public BLEClientCallbacks {
    void onConnect(BLEClient* pclient) override {
        Serial.println("[BLE] Connected");
        g_connected = true;
        posture_set_ble_connected(true);
    }
    void onDisconnect(BLEClient* pclient) override {
        Serial.println("[BLE] Disconnected");
        g_connected = false;
        g_angle_char = nullptr;
        g_vibrate_char = nullptr;
        posture_set_ble_connected(false);
    }
};

class AdvertisedDeviceCallback : public BLEAdvertisedDeviceCallbacks {
    void onResult(BLEAdvertisedDevice advertisedDevice) override {
        String name = advertisedDevice.getName().c_str();
        String addr = advertisedDevice.getAddress().toString().c_str();
        if (name.length() > 0)
            Serial.printf("[BLE] Seen: '%s' [%s]\n", name.c_str(), addr.c_str());
        if (name == BLE_DEVICE_NAME) {
            Serial.printf("[BLE] Found %s, signaling connect task...\n", BLE_DEVICE_NAME);
            BLEDevice::getScan()->stop();
            // Copy device so it's valid after this callback returns
            if (g_target_device) delete g_target_device;
            g_target_device = new BLEAdvertisedDevice(advertisedDevice);
            xSemaphoreGive(g_connect_sem);
        }
    }
};

static void buzz_task(void* arg) {
    while (true) {
        xSemaphoreTake(g_buzz_sem, portMAX_DELAY);
        if (g_connected && g_vibrate_char) {
            uint8_t on = 0x01, off = 0x00;
            g_vibrate_char->writeValue(&on, 1, false);
            vTaskDelay(pdMS_TO_TICKS(BUZZ_DURATION_MS));
            g_vibrate_char->writeValue(&off, 1, false);
            Serial.println("[BLE] Buzz fired");
        }
        if (on_buzz_request) on_buzz_request();
    }
}

static void connect_to_device() {
    if (!g_target_device) return;

    if (!g_client) {
        g_client = BLEDevice::createClient();
        g_client->setClientCallbacks(new ClientCallback());
    }

    Serial.printf("[BLE] Connecting to %s...\n", g_target_device->getAddress().toString().c_str());
    if (!g_client->connect(g_target_device)) {
        Serial.println("[BLE] Connection failed");
        return;
    }
    Serial.println("[BLE] Connected, discovering services...");

    for (auto& [uuid_str, pService] : *g_client->getServices()) {
        for (auto& [char_uuid_str, pChar] : *pService->getCharacteristics()) {
            String uuid = pChar->getUUID().toString();
            if (uuid == CHAR_UUID_ANGLE) {
                g_angle_char = pChar;
                if (pChar->canNotify())
                    pChar->registerForNotify(angle_notify_cb);
                Serial.println("[BLE] Subscribed to angle");
            } else if (uuid == CHAR_UUID_VIBRATE) {
                g_vibrate_char = pChar;
                Serial.println("[BLE] Found vibrate char");
            }
        }
    }
}

static void scan_task(void* arg) {
    BLEScan* pScan = BLEDevice::getScan();
    pScan->setAdvertisedDeviceCallbacks(new AdvertisedDeviceCallback());
    pScan->setActiveScan(true);
    pScan->setInterval(100);
    pScan->setWindow(99);

    while (true) {
        if (!g_connected) {
            Serial.println("[BLE] Scanning...");
            pScan->start(BLE_SCAN_DURATION_S, false);
            // Wait for a device found signal or timeout
            if (xSemaphoreTake(g_connect_sem, pdMS_TO_TICKS(BLE_SCAN_DURATION_S * 1000 + 1000)) == pdTRUE) {
                connect_to_device();
            }
            if (!g_connected)
                vTaskDelay(pdMS_TO_TICKS(BLE_RECONNECT_DELAY_MS));
        } else {
            vTaskDelay(pdMS_TO_TICKS(5000));
        }
    }
}

void ble_client_start() {
    BLEDevice::init("");
    g_buzz_sem = xSemaphoreCreateBinary();
    g_connect_sem = xSemaphoreCreateBinary();

    xTaskCreatePinnedToCore(buzz_task, "buzz", 2048, nullptr, 5, nullptr, 0);
    xTaskCreatePinnedToCore(scan_task, "scan", 12288, nullptr, 3, nullptr, 0);

    Serial.println("[BLE] Started");
}

void ble_buzz() {
    g_buzz_queued = true;
    xSemaphoreGive(g_buzz_sem);
}

bool ble_is_connected() {
    return g_connected;
}
