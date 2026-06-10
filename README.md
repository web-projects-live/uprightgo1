# Upright GO 1 — Fix "Won't Connect" / App Discontinued (Open Source Replacement)

If your **Upright GO 1 posture trainer** has stopped working because the
official Upright app was discontinued or won't connect to your phone anymore,
this project gets it working again — three ways, depending on what hardware
you have lying around.

![Dashboard screenshot showing posture ring, stats, and exercise cards](https://raw.githubusercontent.com/web-projects-live/upright-go-1/master/static/screenshot.png)

It connects directly to the device over Bluetooth (BLE), buzzes you when you
slouch, and tracks your posture progress through a mobile-responsive web
dashboard — no official app, no account, no cloud required.

Not affiliated with Upright Technologies.

---

## Which version should I use?

| You have... | Use this | What it does |
|---|---|---|
| **A spare ESP32 dev board** | **[`esp32-cpp/`](esp32-cpp/)** ✅ recommended | Standalone C++ (PlatformIO/Arduino) firmware. Flash it once — it connects to your Upright GO over BLE, joins your Wi-Fi, and serves the dashboard at `http://uprightgo.local`. No PC needs to stay on. |
| **A Windows/Mac/Linux PC, or a Raspberry Pi** | **[`app.py`](#installation)** (this repo's root) | Python + Flask app using `bleak` for BLE. Run it on any computer with Bluetooth; open the dashboard in a browser on your phone or PC. |
| **An ESP32, and prefer MicroPython** | **[`esp32/`](esp32/)** (legacy/experimental) | Earlier MicroPython firmware for ESP32. Functional but less polished than `esp32-cpp` — kept for reference and for anyone already using it. |

If you're not sure: most people should use **`esp32-cpp`** if they have an
ESP32, or `app.py` on a PC/Pi if they don't.

---

## Features

- **Live posture monitoring** — large visual indicator flips green/red in real time
- **Vibration alerts** — device buzzes when slouching is detected (configurable cooldown)
- **Session stats** — posture score %, session duration, slouch count
- **7-day history** — bar chart + table of daily posture scores
- **All-time stats** — total days trained, hours, average score
- **Guided exercises** — 6 curated exercise cards linking to YouTube searches
- **Settings** — adjustable buzz cooldown (3s–60s), persisted to storage
- **Auto-reconnect** — recovers from Bluetooth drops without restarting
- **Mobile-responsive** — works in any phone or desktop browser

## Credits

BLE protocol reverse-engineered by [niltonheck/upright-go-1-reverse-engineering](https://github.com/niltonheck/upright-go-1-reverse-engineering). This project builds on that research to create a full replacement for the discontinued Upright app.

---

## Safety

The CC2540 chip inside the Upright GO 1 accepts firmware updates over BLE **without integrity checks**. Writing to the wrong characteristic can permanently brick the device (the original researcher lost their device this way).

This app only ever touches two documented characteristics:

| UUID | Direction | Purpose |
|------|-----------|---------|
| `0000aaca-0000-1000-8000-00805f9b34fb` | Read / Notify | Posture angle — **never written to** |
| `0000aad3-0000-1000-8000-00805f9b34fb` | Write | Vibration motor — only `0x01` (on) or `0x00` (off) |

**Do not add writes to any other UUID.**

---

## Installation

### Option 1: Standalone ESP32 (recommended)

See **[`esp32-cpp/BUILD.md`](esp32-cpp/BUILD.md)** for full instructions.
Flash a $5 ESP32 board once and it runs the whole thing — BLE connection,
slouch detection, and web dashboard — with no computer needed afterwards.

### Option 2: Windows / Mac / Linux / Raspberry Pi

```bash
# 1. Install dependencies
pip install bleak flask

# 2. Clone the repo
git clone https://github.com/web-projects-live/upright-go-1.git
cd upright-go-1

# 3. Run
python app.py
```

Open **http://localhost:5000** in your browser.
On your phone (same Wi-Fi): **http://\<your-pc-ip\>:5000** — the IP is printed on startup.

### Option 3: Android (Termux)

> **Note:** BLE on Android/Termux requires Python 3.11+ and may need Bluetooth permissions granted to Termux via Android Settings. If scanning fails, use the PC + phone-browser approach instead (run `python app.py` on your PC and open the dashboard URL in your phone's browser).

```bash
# Install Termux from F-Droid (not the Play Store — the Play Store version is outdated)
# https://f-droid.org/packages/com.termux/

pkg update && pkg install python git
pip install bleak flask
git clone https://github.com/web-projects-live/upright-go-1.git
cd upright-go-1
python app.py
```

Then open **http://localhost:5000** in your phone's browser.

### CLI only (no web UI)

If you just want the buzzer without the dashboard:

```bash
python slouch_buzzer.py
```

---

## Device setup

1. Charge your Upright GO 1 until the LED stops flashing
2. Turn it on with a long press — the LED will flash green
3. **Do not pair through Windows/Android Bluetooth settings** — the app connects directly and pairing causes connection failures
4. Sit up straight and **double-press the button** to calibrate your upright baseline
5. Start the app for your platform (above) — the device is found automatically

---

## Usage

| Element | Meaning |
|---------|---------|
| Green ring | Good posture |
| Red pulsing ring | Slouching detected |
| Score % | Percentage of session time spent upright |
| Buzz cooldown | Minimum gap between vibration alerts |

History is saved automatically at the end of each session (sessions under 30 seconds are discarded).

---

## Troubleshooting / FAQ

**"My Upright GO 1 app doesn't work / was removed from the app store"** —
This is expected; Upright Technologies discontinued the official app. This
project replaces it entirely — see "Which version should I use?" above.

**"My Upright GO won't connect / pair via Bluetooth"** —
Don't pair it through your phone's or PC's Bluetooth settings — leave it
unpaired there. This software connects directly via BLE. If it was previously
paired, remove/forget it from your OS Bluetooth settings first.

**"The device shows a green flashing light but nothing connects"** —
Sit up straight and double-press the button on the device to calibrate —
this is also how the original app initiated a connection, and these tools
rely on the device advertising as `UprightGO`.

**"It connects but won't buzz / no angle data"** —
Make sure nothing else (your phone, the official app, Windows Bluetooth)
is currently connected to the device — BLE devices like this only accept
one connection at a time.

**"Can I run this on a Raspberry Pi?"** —
Yes — any Linux machine with Bluetooth (including Pi Zero W / Pi 4) works
with `app.py` (Option 2 above).

---

## BLE characteristics reference

From [niltonheck/upright-go-1-reverse-engineering](https://github.com/niltonheck/upright-go-1-reverse-engineering):

| Short UUID | Full UUID | Type | Function |
|------------|-----------|------|---------|
| `aaca` | `0000aaca-0000-1000-8000-00805f9b34fb` | Notify/Read | Angle data; last byte `0x02` = slouching |
| `aad3` | `0000aad3-0000-1000-8000-00805f9b34fb` | Write | Vibration (`0x01` on, `0x00` off) |
| `aab1` | `0000aab1-0000-1000-8000-00805f9b34fb` | Write | Calibration (same as double-press) |
| `aab3` | `0000aab3-0000-1000-8000-00805f9b34fb` | Read | 4-byte angle + accelerometer data |
| `aac6` | `0000aac6-0000-1000-8000-00805f9b34fb` | Notify/Read | Button press |

---

## License

MIT
