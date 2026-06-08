# Upright GO 1 — Open Source Dashboard

An open-source replacement for the discontinued official Upright GO 1 app. Connects to your device over Bluetooth, buzzes you when you slouch, and tracks your posture progress through a mobile-responsive web dashboard.

![Dashboard screenshot showing posture ring, stats, and exercise cards](https://raw.githubusercontent.com/web-projects-live/upright-go-1/master/static/screenshot.png)

## Features

- **Live posture monitoring** — large visual indicator flips green/red in real time
- **Vibration alerts** — device buzzes when slouching is detected (configurable cooldown)
- **Session stats** — posture score %, session duration, slouch count
- **7-day history** — bar chart + table of daily posture scores
- **All-time stats** — total days trained, hours, average score
- **Guided exercises** — 6 curated exercise cards linking to YouTube searches
- **Settings** — adjustable buzz cooldown (3s–60s), persisted to SQLite
- **Auto-reconnect** — recovers from Bluetooth drops without restarting
- **Mobile-responsive** — works in any phone or desktop browser

## Credits

BLE protocol reverse-engineered by [niltonheck/upright-go-1-reverse-engineering](https://github.com/niltonheck/upright-go-1-reverse-engineering). This project builds on that research to create a full replacement for the discontinued Upright app.

Not affiliated with Upright Technologies.

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

### Windows / Mac / Linux

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

### Android (Termux)

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
5. Run `python app.py` — the device is found automatically

---

## Usage

| Element | Meaning |
|---------|---------|
| Green ring | Good posture |
| Red pulsing ring | Slouching detected |
| Score % | Percentage of session time spent upright |
| Buzz cooldown | Minimum gap between vibration alerts |

History is saved automatically at the end of each session (sessions under 30 seconds are discarded). The database lives at `posture.db` in the project folder.

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
