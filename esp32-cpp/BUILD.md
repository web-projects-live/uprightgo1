# Building the Upright GO C++ Firmware (ESP32 standalone device)

This is the **recommended firmware for a standalone ESP32 board** — it runs the
BLE connection, slouch detection, and a Wi-Fi web dashboard entirely on the
chip, with no PC required after setup.

## Requirements

- [PlatformIO](https://platformio.org/) (CLI or VS Code extension)
- ESP32 dev board (tested on `esp32dev` / WROOM-32, 4MB flash)
- USB-UART cable (CP210x or CH340)
- Your Upright GO 1, charged and powered on

## Build

```bash
cd esp32-cpp
pio run
```

This downloads `mathieucarbou/AsyncTCP` + `ESPAsyncWebServer` (the
framework-3.x-compatible forks — the `me-no-dev` originals will crash with a
`tcp_alloc` assertion on current `framework-arduinoespressif32`), plus
ArduinoJson, and compiles with `-std=gnu++17`.

## Flash

`pio run -t upload` can hit a `UnicodeEncodeError` in some Windows terminals.
If that happens, flash manually with `esptool` instead (adjust `COM4` to your
port):

```bash
python -m esptool --chip esp32 --port COM4 --baud 921600 \
  --before default-reset --after hard-reset write-flash -z \
  --flash-mode dio --flash-freq 40m --flash-size 4MB \
  0x1000  .pio/build/esp32/bootloader.bin \
  0x8000  .pio/build/esp32/partitions.bin \
  0x10000 .pio/build/esp32/firmware.bin
```

## First boot

1. Device starts AP mode: **`UprightGO-Setup`** (open, no password)
2. Connect to it, visit **http://192.168.4.1**
3. Enter your Wi-Fi SSID + password
4. Device reboots into STA mode and registers **http://uprightgo.local**
5. Open that link in your browser → dashboard appears, BLE auto-connects to
   "UprightGO"

## Project structure

```
esp32-cpp/
├── platformio.ini          Board, libraries, build flags
├── src/
│   ├── main.cpp            Entry point — wires WiFi, BLE, web server, posture engine
│   ├── config.h            All constants (UUIDs, defaults, pins)
│   ├── ble_client.cpp/h    Arduino BLE scanner + GATT client (FreeRTOS tasks)
│   ├── posture.cpp/h       Sliding-window slouch detection
│   ├── web_server.cpp/h    ESPAsyncWebServer + REST API + SSE push
│   ├── wifi_manager.cpp/h  STA/AP mode, mDNS registration, captive portal DNS
│   └── storage.cpp/h       NVS settings + LittleFS history + JSON persistence
└── data/
    ├── index.html          Single-page app (setup portal + dashboard)
    ├── app.js              SSE-driven state updates, Chart.js history
    └── style.css           Dark theme, responsive, pulsing ring states
```

## Notes / gotchas

- BLE uses the stock Arduino-ESP32 `BLEDevice` API (Bluedroid), **not NimBLE**.
- The BLE scan/connect task needs a 12KB stack
  (`xTaskCreatePinnedToCore(scan_task, "scan", 12288, ...)` in
  `ble_client.cpp`) — 4KB is too small and causes a stack-overflow crash
  (`assert failed: hash_map_set hash_map.c:129`) the first time it tries to
  connect and discover services.
- `BLEAdvertisedDevice` passed into `onResult()` is a stack reference that's
  invalid after the callback returns — heap-copy it
  (`new BLEAdvertisedDevice(advertisedDevice)`) before handing it to another
  task.
