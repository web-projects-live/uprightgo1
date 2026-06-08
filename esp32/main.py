"""
Upright GO 1 — ESP32 MicroPython firmware
Connects to Upright GO 1 via BLE, serves web dashboard over Wi-Fi on port 80.

Upload all files in esp32/ to the root of the ESP32 filesystem.
Copy config.py.example -> config.py and fill in your Wi-Fi credentials.

SAFETY: Only reads aaca (angle) and writes 0x00/0x01 to aad3 (vibration).
Credits: BLE protocol by niltonheck/upright-go-1-reverse-engineering
"""

import ubluetooth
import uasyncio as asyncio
import ujson
import network
import time
import struct
import gc

# ── Config ────────────────────────────────────────────────────────────────────
try:
    import config
    WIFI_SSID = config.WIFI_SSID
    WIFI_PASS = config.WIFI_PASS
except ImportError:
    WIFI_SSID = "YOUR_WIFI_SSID"
    WIFI_PASS = "YOUR_WIFI_PASSWORD"

DEVICE_NAME   = "UprightGO"
POLL_INTERVAL = 0.15
HISTORY_FILE  = "history.json"
SETTINGS_FILE = "settings.json"
MAX_HISTORY   = 30

DEFAULT_SETTINGS = {
    "sensitivity":     "normal",
    "slouch_window_s": 5.0,
    "slouch_threshold": 0.60,
    "buzz_cooldown":   15.0,
    "daily_goal_min":  20,
    "slouch_angle":    10,
}

PRESETS = {
    "lenient": {"slouch_window_s": 8.0,  "slouch_threshold": 0.70, "buzz_cooldown": 30.0},
    "normal":  {"slouch_window_s": 5.0,  "slouch_threshold": 0.60, "buzz_cooldown": 15.0},
    "strict":  {"slouch_window_s": 2.5,  "slouch_threshold": 0.50, "buzz_cooldown":  8.0},
}

# ── BLE UUIDs / IRQ constants ─────────────────────────────────────────────────
ANGLE_UUID     = ubluetooth.UUID("0000aaca-0000-1000-8000-00805f9b34fb")
VIBRATION_UUID = ubluetooth.UUID("0000aad3-0000-1000-8000-00805f9b34fb")

_IRQ_SCAN_RESULT                 = 5
_IRQ_SCAN_DONE                   = 6
_IRQ_PERIPHERAL_CONNECT          = 7
_IRQ_PERIPHERAL_DISCONNECT       = 8
_IRQ_GATTC_CHARACTERISTIC_RESULT = 11
_IRQ_GATTC_CHARACTERISTIC_DONE   = 12
_IRQ_GATTC_READ_RESULT           = 15
_IRQ_GATTC_READ_DONE             = 16
_IRQ_GATTC_WRITE_DONE            = 17

# ── Application state ─────────────────────────────────────────────────────────
settings = {}

state = {
    "connected":     False,
    "posture":       "unknown",
    "slouch_count":  0,
    "good_seconds":  0.0,
    "total_seconds": 0.0,
    "mode":          "desk",
    "slouch_ratio":  0.0,
    "score":         0,
}

_slouch_samples = []
_clear_window   = False
_last_buzz      = 0.0

# BLE internals
_ble              = None
_ble_state        = "idle"   # idle | scanning | connecting | discovering | ready
_conn_handle      = None
_angle_handle     = None
_vibration_handle = None
_found_addr       = None
_found_addr_type  = None
_chars            = {}
_pending_read     = False
_read_result      = None

# ── Persistence ───────────────────────────────────────────────────────────────
def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE) as f:
            settings = ujson.load(f)
    except Exception:
        settings = dict(DEFAULT_SETTINGS)
    for k, v in DEFAULT_SETTINGS.items():
        if k not in settings:
            settings[k] = v

def save_settings_file():
    with open(SETTINGS_FILE, "w") as f:
        ujson.dump(settings, f)

def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return ujson.load(f)
    except Exception:
        return []

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        ujson.dump(history[-MAX_HISTORY:], f)

def record_session():
    if state["total_seconds"] < 30:
        return
    history = load_history()
    tot = state["total_seconds"]
    score = int(state["good_seconds"] / tot * 100) if tot > 0 else 0
    t = time.localtime()
    history.append({
        "date":             f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}",
        "score":            score,
        "duration_minutes": round(tot / 60, 1),
        "slouch_count":     state["slouch_count"],
    })
    save_history(history)

def get_coaching():
    history = load_history()
    goal_min = settings["daily_goal_min"]

    if not history:
        return {
            "phase": "beginner", "streak_days": 0, "seven_day_avg": 0,
            "best_score": 0, "trend": "flat", "graduation_days": 0,
            "today_minutes": round(state["total_seconds"] / 60, 1),
            "daily_goal_min": goal_min,
            "tip": "Start your first session to begin tracking!",
        }

    scores = [h["score"] for h in history[-7:]]
    avg  = int(sum(scores) / len(scores)) if scores else 0
    best = max((h["score"] for h in history), default=0)

    if len(scores) >= 4:
        mid  = len(scores) // 2
        avg1 = sum(scores[:mid]) / mid
        avg2 = sum(scores[mid:]) / (len(scores) - mid)
        trend = "improving" if avg2 - avg1 > 5 else "declining" if avg1 - avg2 > 5 else "flat"
    else:
        trend = "flat"

    phase = "advanced" if avg >= 80 else "intermediate" if avg >= 60 else "beginner"

    grad_days = sum(
        1 for h in history[-30:]
        if h["score"] >= 75 and h["duration_minutes"] >= goal_min
    )
    if grad_days >= 7:
        phase = "graduated"

    t = time.localtime()
    today_str  = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"
    today_min  = round(state["total_seconds"] / 60, 1)
    today_min += sum(h["duration_minutes"] for h in history if h["date"] == today_str)

    tips = [
        "Sit with your back against the chair back for support.",
        "Set a reminder to stand up every 30 minutes.",
        "Keep your monitor at eye level to avoid neck strain.",
        "Roll your shoulders back and down, not up.",
        "Good posture is a habit. Consistency beats perfection.",
        "Chin tucks strengthen the deep neck flexors — do 10 daily.",
        "Wall angels open shoulders and mobilise the thoracic spine.",
    ]
    tip = tips[len(history) % len(tips)]

    return {
        "phase": phase, "streak_days": min(len(history), 7),
        "seven_day_avg": avg, "best_score": best, "trend": trend,
        "graduation_days": min(grad_days, 7),
        "today_minutes": round(today_min, 1), "daily_goal_min": goal_min,
        "tip": tip,
    }

def get_alltime():
    history = load_history()
    days    = len(history)
    total_h = round(sum(h["duration_minutes"] for h in history) / 60, 1)
    avg     = int(sum(h["score"] for h in history) / days) if days else 0
    return {"days": days, "total_hours": total_h, "avg_score": avg}

# ── BLE IRQ ───────────────────────────────────────────────────────────────────
def _parse_adv_name(adv_data):
    i = 0
    while i < len(adv_data):
        if i + 1 >= len(adv_data):
            break
        length = adv_data[i]
        if length == 0:
            break
        ad_type = adv_data[i + 1]
        if ad_type in (0x08, 0x09):
            try:
                return adv_data[i + 2: i + 1 + length].decode("utf-8")
            except Exception:
                pass
        i += 1 + length
    return None

def _ble_irq(event, data):
    global _ble_state, _conn_handle, _found_addr, _found_addr_type
    global _angle_handle, _vibration_handle, _chars
    global _pending_read, _read_result

    if event == _IRQ_SCAN_RESULT:
        addr_type, addr, adv_type, rssi, adv_data = data
        name = _parse_adv_name(bytes(adv_data))
        if name and DEVICE_NAME in name:
            print("Found", name)
            _found_addr_type = addr_type
            _found_addr      = bytes(addr)
            _ble.gap_scan(None)

    elif event == _IRQ_SCAN_DONE:
        if _found_addr and _ble_state == "scanning":
            _ble_state = "connecting"
            _ble.gap_connect(_found_addr_type, _found_addr)
        else:
            _ble_state = "idle"

    elif event == _IRQ_PERIPHERAL_CONNECT:
        conn_handle, addr_type, addr = data
        _conn_handle = conn_handle
        _ble_state   = "discovering"
        _chars       = {}
        print("Connected, discovering characteristics...")
        _ble.gattc_discover_characteristics(_conn_handle, 0x0001, 0xFFFF)

    elif event == _IRQ_PERIPHERAL_DISCONNECT:
        _conn_handle      = None
        _angle_handle     = None
        _vibration_handle = None
        _ble_state        = "idle"
        _chars            = {}
        state["connected"] = False
        state["posture"]   = "unknown"
        print("Disconnected")

    elif event == _IRQ_GATTC_CHARACTERISTIC_RESULT:
        conn_handle, def_handle, value_handle, properties, uuid = data
        _chars[str(uuid).lower()] = value_handle

    elif event == _IRQ_GATTC_CHARACTERISTIC_DONE:
        angle_key = str(ANGLE_UUID).lower()
        vibr_key  = str(VIBRATION_UUID).lower()
        if angle_key in _chars and vibr_key in _chars:
            _angle_handle     = _chars[angle_key]
            _vibration_handle = _chars[vibr_key]
            _ble_state        = "ready"
            state["connected"] = True
            print("Ready! angle={} vibration={}".format(_angle_handle, _vibration_handle))
        else:
            print("Required characteristics not found, will retry")
            _ble_state = "idle"

    elif event == _IRQ_GATTC_READ_RESULT:
        conn_handle, value_handle, char_data = data
        _read_result  = bytes(char_data)
        _pending_read = False

    elif event == _IRQ_GATTC_READ_DONE:
        conn_handle, value_handle, status = data
        if status != 0:
            _pending_read = False

# ── Posture processing ────────────────────────────────────────────────────────
def _process_angle(data):
    global _last_buzz, _slouch_samples, _clear_window

    if _clear_window:
        _slouch_samples.clear()
        _clear_window = False

    angle     = struct.unpack_from(">h", data, 0)[0] / 100.0
    raw_slouch = angle > settings.get("slouch_angle", 10)

    window_s  = settings["slouch_window_s"]
    threshold = settings["slouch_threshold"]
    max_polls = max(1, int(window_s / POLL_INTERVAL))

    _slouch_samples.append(1 if raw_slouch else 0)
    if len(_slouch_samples) > max_polls * 2:
        _slouch_samples = _slouch_samples[-max_polls:]

    recent       = _slouch_samples[-max_polls:]
    ratio        = sum(recent) / len(recent) if recent else 0.0
    window_ready = len(recent) >= max(3, max_polls // 3)
    is_slouching = window_ready and ratio >= threshold

    state["slouch_ratio"] = round(ratio, 3)
    state["posture"]      = "slouching" if is_slouching else "good"
    state["total_seconds"] += POLL_INTERVAL
    if not is_slouching:
        state["good_seconds"] += POLL_INTERVAL

    tot = state["total_seconds"]
    state["score"] = int(state["good_seconds"] / tot * 100) if tot > 0 else 100

    now = time.time()
    if is_slouching and state["mode"] == "desk" and now - _last_buzz >= settings["buzz_cooldown"]:
        _last_buzz = now
        state["slouch_count"] += 1
        asyncio.get_event_loop().create_task(_buzz())

async def _buzz():
    if _vibration_handle is None or _conn_handle is None:
        return
    try:
        _ble.gattc_write(_conn_handle, _vibration_handle, bytes([0x01]), 1)
        await asyncio.sleep(0.5)
        _ble.gattc_write(_conn_handle, _vibration_handle, bytes([0x00]), 1)
    except Exception as e:
        print("Buzz error:", e)

# ── BLE task ──────────────────────────────────────────────────────────────────
async def ble_task():
    global _ble, _ble_state, _found_addr, _found_addr_type
    global _pending_read, _read_result

    _ble = ubluetooth.BLE()
    _ble.active(True)
    _ble.irq(_ble_irq)

    while True:
        if _ble_state == "idle":
            _found_addr = None
            print("Scanning for UprightGO...")
            _ble_state = "scanning"
            _ble.gap_scan(10000, 30000, 30000)
            await asyncio.sleep(13)
            if _ble_state == "scanning":
                _ble.gap_scan(None)
                _ble_state = "idle"
            await asyncio.sleep(5)

        elif _ble_state == "ready":
            _pending_read = True
            _read_result  = None
            try:
                _ble.gattc_read(_conn_handle, _angle_handle)
            except Exception as e:
                print("Read error:", e)
                _ble_state = "idle"
                await asyncio.sleep(1)
                continue

            deadline = time.ticks_ms() + 1000
            while _pending_read and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                await asyncio.sleep(0.05)

            if _read_result and len(_read_result) >= 2:
                _process_angle(_read_result)

            await asyncio.sleep(POLL_INTERVAL)

        else:
            await asyncio.sleep(0.5)

# ── Wi-Fi ─────────────────────────────────────────────────────────────────────
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Connecting to Wi-Fi:", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASS)
        deadline = time.time() + 20
        while not wlan.isconnected() and time.time() < deadline:
            time.sleep(0.5)
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("Wi-Fi OK: http://{}".format(ip))
        return ip
    # Fall back to AP mode
    print("Wi-Fi failed — AP mode: UprightGO / posture123")
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="UprightGO", password="posture123")
    return "192.168.4.1"

# ── HTTP server ───────────────────────────────────────────────────────────────
def _read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None

def _resp(status, ctype, body):
    if isinstance(body, str):
        body = body.encode()
    hdr = "HTTP/1.0 {} OK\r\nContent-Type: {}\r\nContent-Length: {}\r\n\r\n".format(
        status, ctype, len(body))
    return hdr.encode() + body

def _json(data, status=200):
    return _resp(status, "application/json", ujson.dumps(data))

def _route(method, path, body_bytes):
    global _clear_window

    # Static files
    if path in ("/", "/index.html"):
        f = _read_file("index.html")
        return _resp(200, "text/html", f) if f else _resp(404, "text/plain", b"Not found")
    if path == "/style.css":
        f = _read_file("style.css")
        return _resp(200, "text/css", f) if f else _resp(404, "text/plain", b"Not found")
    if path == "/app.js":
        f = _read_file("app.js")
        return _resp(200, "application/javascript", f) if f else _resp(404, "text/plain", b"Not found")

    # API
    if path == "/api/status":
        tot = state["total_seconds"]
        return _json({
            "connected":     state["connected"],
            "posture":       state["posture"],
            "slouch_count":  state["slouch_count"],
            "score":         state["score"],
            "total_minutes": round(tot / 60, 1),
            "mode":          state["mode"],
            "slouch_ratio":  state["slouch_ratio"],
        })

    if path == "/api/history":
        return _json(load_history()[-7:])

    if path == "/api/coaching":
        return _json(get_coaching())

    if path == "/api/alltime":
        return _json(get_alltime())

    if path == "/api/settings":
        if method == "GET":
            return _json(settings)
        try:
            data   = ujson.loads(body_bytes) if body_bytes else {}
            preset = data.get("sensitivity")
            if preset and preset in PRESETS:
                settings.update(PRESETS[preset])
                settings["sensitivity"] = preset
            for k in ("slouch_window_s", "slouch_threshold", "buzz_cooldown",
                      "daily_goal_min", "slouch_angle"):
                if k in data:
                    settings[k] = data[k]
            save_settings_file()
            _clear_window = True
            return _json({"ok": True})
        except Exception as e:
            return _json({"error": str(e)}, 400)

    if path == "/api/mode" and method == "POST":
        try:
            data = ujson.loads(body_bytes) if body_bytes else {}
            mode = data.get("mode", "desk")
            if mode in ("desk", "moving", "break"):
                state["mode"] = mode
                if mode == "desk":
                    _clear_window = True
            return _json({"ok": True})
        except Exception as e:
            return _json({"error": str(e)}, 400)

    return _resp(404, "text/plain", b"Not found")

async def _handle(reader, writer):
    try:
        req_line = await asyncio.wait_for(reader.readline(), 5)
        if not req_line:
            return
        headers = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), 3)
            if not line or line == b"\r\n":
                break
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().lower()] = v.strip()

        cl   = int(headers.get(b"content-length", b"0"))
        body = await reader.read(cl) if cl else b""

        parts = req_line.decode().strip().split()
        if len(parts) < 2:
            return
        method, path = parts[0], parts[1].split("?")[0]

        response = _route(method, path, body)
        writer.write(response)
        await writer.drain()
    except Exception as e:
        print("HTTP err:", e)
    finally:
        writer.close()
        await writer.wait_closed()
        gc.collect()

async def web_server():
    server = await asyncio.start_server(_handle, "0.0.0.0", 80)
    print("Web server on port 80")
    async with server:
        await server.wait_closed()

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    load_settings()
    connect_wifi()
    loop = asyncio.get_event_loop()
    loop.create_task(ble_task())
    loop.create_task(web_server())
    # Save session every 5 minutes then reset counters
    while True:
        await asyncio.sleep(300)
        if state["connected"] and state["total_seconds"] > 60:
            record_session()
            state["total_seconds"] = 0.0
            state["good_seconds"]  = 0.0
            state["slouch_count"]  = 0

asyncio.run(main())
