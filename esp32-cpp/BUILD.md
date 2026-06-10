# Building Upright Go C++ Firmware

## Quick start

**Using your existing ESPHome toolchain (recommended):**

```bash
cd C:\Users\Joel
python -m esphome run "C:\Users\Joel\Documents\Upright Go\esp32-cpp\platformio.ini" --device COM4
```

This uses the exact same `xtensa-esp-elf-gcc`, ESP-IDF, and other tools cached in `C:\Users\Joel\.platformio\packages\` — same toolchain you already use for pool-pump-esp.

---

## Project structure

```
esp32-cpp/
├── platformio.ini          Configuration (platform, board, port, libraries)
├── src/
│   ├── main.cpp            Entry point — wires WiFi, BLE, web server, posture engine
│   ├── config.h            All constants (UUIDs, defaults, pins)
│   ├── ble_client.cpp/h    NimBLE scanner + GATT client (FreeRTOS tasks)
│   ├── posture.cpp/h       Sliding-window slouch detection (mutex-protected state)
│   ├── web_server.cpp/h    ESPAsyncWebServer + REST API + SSE push
│   ├── wifi_manager.cpp/h  STA/AP mode, mDNS registration, captive portal DNS
│   └── storage.cpp/h       NVS settings + LittleFS history + JSON persistence
└── data/
    ├── index.html          Single-page app (setup portal + dashboard)
    ├── app.js              SSE-driven state updates, Chart.js history
    └── style.css           Dark theme, responsive, pulsing ring states
```

---

## What gets built

- **`firmware.bin`** — Main application (NimBLE + ESPAsyncWebServer + posture logic)
- **`littlefs.bin`** — LittleFS image (index.html, app.js, style.css baked into flash)

Both are flashed to the ESP32 automatically via the `--device COM4` flag.

---

## First build (will take ~2–5 min)

- Downloads NimBLE, AsyncWebServer, ArduinoJson from PlatformIO registry
- Compiles all C++ with `-std=gnu++17`
- Builds the web UI into LittleFS
- Flashes both binaries to the ESP32

Subsequent builds are much faster (cached libraries).

---

## After flashing

1. Device starts AP mode: `UprightGO-Setup` (open, no password)
2. Connect to it, visit `http://192.168.4.1`
3. Enter your Wi-Fi SSID + password
4. Device reboots into STA mode, registers `http://uprightgo.local`
5. Open that link in your browser → dashboard appears

---

## If the build hangs at "Compiling..."

(This was an issue with PlatformIO's `uv` timeout on Windows, but shouldn't happen with the cached toolchain.)

Kill the process and retry:
```bash
python -m esphome run "C:\Users\Joel\Documents\Upright Go\esp32-cpp\platformio.ini" --device COM4
```

---

## Hardware requirements

- ESP32 (tested on esp32dev, WROOM-32)
- 4 MB flash minimum
- USB-UART bridge (CP210x or CH340)
- Upright GO 1 device (for BLE connection)
