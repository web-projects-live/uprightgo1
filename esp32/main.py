"""
Upright GO 1 — ESP32 MicroPython firmware
Connects to Upright GO 1 via BLE, serves web dashboard over Wi-Fi on port 80.

First boot: ESP32 starts a hotspot called "UprightGO-Setup" (no password).
Connect to it and open http://192.168.4.1 to configure Wi-Fi — or skip Wi-Fi
entirely and just use the hotspot directly. Settings are saved to the device.

Wi-Fi is optional: the dashboard and BLE posture tracking work fine over the
built-in hotspot with no router involved.

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

# ── Wi-Fi config (saved to device, never hard-coded) ─────────────────────────
WIFI_CONFIG_FILE = "wifi.json"

def _load_wifi():
    try:
        with open(WIFI_CONFIG_FILE) as f:
            d = ujson.load(f)
            return d.get("ssid", ""), d.get("password", "")
    except Exception:
        pass
    # Migrate from old config.py if present
    try:
        import config as _cfg
        if hasattr(_cfg, "WIFI_SSID") and _cfg.WIFI_SSID not in ("", "YOUR_WIFI_SSID"):
            _save_wifi(_cfg.WIFI_SSID, getattr(_cfg, "WIFI_PASS", ""))
            return _cfg.WIFI_SSID, getattr(_cfg, "WIFI_PASS", "")
    except Exception:
        pass
    return "", ""

def _save_wifi(ssid, password):
    with open(WIFI_CONFIG_FILE, "w") as f:
        ujson.dump({"ssid": ssid, "password": password}, f)

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
    global _ble_state, _found_addr, _found_addr_type
    global _pending_read, _read_result
    # BLE already initialized before asyncio started — just start scanning
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

# ── Wi-Fi setup (runs synchronously BEFORE asyncio, nothing can interfere) ────

_SETUP_HTML = b"""\
HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n\
<!DOCTYPE html><html><head><meta charset="UTF-8">\
<meta name="viewport" content="width=device-width,initial-scale=1">\
<title>Upright GO Setup</title>\
<style>*{box-sizing:border-box}body{font-family:sans-serif;background:#1a1d2e;\
color:#e2e8f0;display:flex;align-items:center;justify-content:center;\
min-height:100vh;margin:0}.card{background:#242840;border-radius:12px;\
padding:2rem;max-width:360px;width:100%}h2{margin:0 0 .5rem}\
p{color:#8892a4;margin:0 0 1.5rem;font-size:.9rem}\
input{width:100%;padding:.7rem 1rem;border-radius:8px;border:1px solid #363b5e;\
background:#1a1d2e;color:#e2e8f0;margin-bottom:.75rem;font-size:1rem}\
button{width:100%;padding:.8rem;border-radius:8px;border:none;cursor:pointer;\
font-size:1rem;font-weight:600;margin-bottom:.5rem}\
.p{background:#7c6aff;color:#fff}.s{background:#363b5e;color:#8892a4}\
</style></head><body><div class="card">\
<h2>Upright GO 1</h2>\
<p>Enter your home Wi-Fi — or skip to use this hotspot only.</p>\
<form method="POST" action="/save">\
<input name="s" placeholder="Wi-Fi name" autocomplete="off">\
<input name="p" type="password" placeholder="Wi-Fi password" autocomplete="off">\
<button class="p" type="submit">Save &amp; connect</button></form>\
<form method="POST" action="/skip">\
<button class="s" type="submit">Skip — hotspot only</button></form>\
</div></body></html>"""

def _url_decode(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i+1:i+3], 16))
                i += 3
                continue
            except Exception:
                pass
        out += s[i]
        i += 1
    return out

def _parse_form(body):
    params = {}
    try:
        for pair in body.decode().split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[_url_decode(k)] = _url_decode(v)
    except Exception:
        pass
    return params

def run_setup_portal():
    """Synchronous setup portal — runs before asyncio, no interference."""
    import socket as _socket
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="UprightGO-Setup", authmode=0)
    time.sleep(1)
    print("Setup portal: connect to UprightGO-Setup, open http://192.168.4.1")

    srv = _socket.socket()
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(1)
    srv.settimeout(120)   # give up after 2 min if nobody connects

    _REDIR  = b"HTTP/1.0 302 Found\r\nLocation: http://192.168.4.1/\r\n\r\n"
    _PORTAL = (b"/generate_204", b"/gen_204", b"/hotspot-detect.html",
               b"/connecttest.txt", b"/ncsi.txt", b"/redirect")

    done = False
    while not done:
        try:
            conn, addr = srv.accept()
            print("Setup: connection from", addr)
        except OSError:
            print("Setup portal timed out, continuing without Wi-Fi")
            break
        try:
            conn.settimeout(5)
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(256)
                if not chunk:
                    break
                req += chunk

            line1 = req.split(b"\r\n")[0]
            parts = line1.split(b" ")
            method = parts[0] if len(parts) > 0 else b""
            path   = parts[1].split(b"?")[0] if len(parts) > 1 else b"/"

            # Captive portal detection → redirect to setup page
            if path in _PORTAL:
                conn.send(_REDIR)

            elif path == b"/save" and method == b"POST":
                cl = 0
                for ln in req.split(b"\r\n"):
                    if ln.lower().startswith(b"content-length:"):
                        cl = int(ln.split(b":")[1].strip())
                body_start = req.find(b"\r\n\r\n") + 4
                body = req[body_start:]
                while len(body) < cl:
                    body += conn.recv(256)
                p = _parse_form(body)
                ssid = p.get("s", "").strip()
                pw   = p.get("p", "")
                if ssid:
                    _save_wifi(ssid, pw)
                    conn.send(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
                              b"<html><body style='font-family:sans-serif;background:#1a1d2e;"
                              b"color:#e2e8f0;padding:2rem'><h2>Saved!</h2>"
                              b"<p>Rebooting to connect to your Wi-Fi...</p></body></html>")
                    done = True
                else:
                    conn.send(_SETUP_HTML)

            elif path == b"/skip" and method == b"POST":
                _save_wifi("", "")
                conn.send(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
                          b"<html><body style='font-family:sans-serif;background:#1a1d2e;"
                          b"color:#e2e8f0;padding:2rem'><h2>OK!</h2>"
                          b"<p>Using hotspot mode. Dashboard starting...</p></body></html>")
                done = True

            else:
                conn.send(_SETUP_HTML)

        except Exception as e:
            print("Setup handler err:", e)
        finally:
            conn.close()

    srv.close()
    if done:
        print("Setup complete — rebooting")
        time.sleep(2)
        import machine
        machine.reset()

def connect_wifi():
    ssid, password = _load_wifi()
    if ssid:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            print("Connecting to Wi-Fi:", ssid)
            wlan.connect(ssid, password)
            deadline = time.time() + 20
            while not wlan.isconnected() and time.time() < deadline:
                time.sleep(0.5)
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print("Wi-Fi OK: http://{}".format(ip))
            return ip
        print("Wi-Fi failed — check credentials")
    # No Wi-Fi config — run synchronous setup portal then reboot
    run_setup_portal()
    # If we get here, portal timed out — continue in hotspot mode
    return "192.168.4.1"

def _parse_form(body_bytes):
    params = {}
    try:
        for pair in body_bytes.decode().split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[_url_decode(k)] = _url_decode(v)
    except Exception:
        pass
    return params

# ── HTTP server ───────────────────────────────────────────────────────────────
# ── Debug log ─────────────────────────────────────────────────────────────────
_log = []
def _dbg(msg):
    print(msg)
    _log.append(msg)
    if len(_log) > 40:
        _log.pop(0)
    try:
        with open("debug.log", "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def _file_size(path):
    try:
        import uos
        return uos.stat(path)[6]
    except Exception:
        return -1

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

    if path == "/api/wifi-status":
        wlan = network.WLAN(network.STA_IF)
        ssid, _ = _load_wifi()
        return _json({"ssid": ssid, "connected": wlan.isconnected(),
                      "ip": wlan.ifconfig()[0] if wlan.isconnected() else ""})

    if path == "/api/wifi-save" and method == "POST":
        try:
            data = ujson.loads(body_bytes) if body_bytes else {}
            _save_wifi(data.get("ssid", ""), data.get("password", ""))
            return _json({"ok": True, "note": "Reboot to apply"})
        except Exception as e:
            return _json({"error": str(e)}, 400)

    # ── Static files ──────────────────────────────────────────────────────────
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

    if path == "/debug":
        import uos
        files = [(f, uos.stat(f)[6]) for f in uos.listdir('/')]
        info = {
            "mem_free": gc.mem_free(),
            "files": files,
            "ble_state": _ble_state,
            "log": _log[-20:],
        }
        return _json(info)

    return _resp(404, "text/plain", b"Not found")

# ── HTTP server (runs in a thread — avoids BLE/asyncio poll conflict) ─────────
_STATIC = {
    "/":           ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/style.css":  ("style.css",  "text/css"),
    "/app.js":     ("app.js",     "application/javascript"),
}

def _handle_sync(conn):
    import uio, sys
    try:
        conn.settimeout(5)
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(256)
            if not chunk:
                break
            req += chunk
            if len(req) > 4096:
                break

        line1 = req.split(b"\r\n")[0]
        parts = line1.split(b" ")
        if len(parts) < 2:
            return
        method = parts[0].decode()
        path   = parts[1].split(b"?")[0].decode()
        _dbg("{} {} mem={}".format(method, path, gc.mem_free()))

        # Read body for POST
        cl = 0
        for ln in req.split(b"\r\n"):
            if ln.lower().startswith(b"content-length:"):
                try: cl = int(ln.split(b":")[1].strip())
                except: pass
        body = req[req.find(b"\r\n\r\n") + 4:]
        while len(body) < cl:
            chunk = conn.recv(256)
            if not chunk: break
            body += chunk

        # Serve static files in chunks
        if path in _STATIC:
            fname, ctype = _STATIC[path]
            sz = _file_size(fname)
            if sz < 0:
                conn.send(b"HTTP/1.0 404 Not Found\r\n\r\nNot found")
                return
            conn.send("HTTP/1.0 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\n\r\n".format(ctype, sz).encode())
            with open(fname, "rb") as f:
                while True:
                    chunk = f.read(512)
                    if not chunk: break
                    conn.send(chunk)
            return

        response = _route(method, path, body)
        conn.send(response)

    except Exception as e:
        buf = uio.StringIO()
        sys.print_exception(e, buf)
        _dbg("HTTP err: " + buf.getvalue())

def _http_server_thread():
    import socket as _socket
    srv = _socket.socket()
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(2)
    print("HTTP server on port 80")
    while True:
        try:
            conn, addr = srv.accept()
            try:
                _handle_sync(conn)
            except Exception as e:
                _dbg("conn err: " + str(e))
            finally:
                conn.close()
                gc.collect()
        except Exception as e:
            _dbg("accept err: " + str(e))
            time.sleep(1)

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    asyncio.get_event_loop().create_task(ble_task())
    while True:
        await asyncio.sleep(300)
        if state["connected"] and state["total_seconds"] > 60:
            record_session()
            state["total_seconds"] = 0.0
            state["good_seconds"]  = 0.0
            state["slouch_count"]  = 0

load_settings()
connect_wifi()

# Start HTTP server in a thread (separate from asyncio — avoids BLE poll conflict)
import _thread
_thread.start_new_thread(_http_server_thread, ())

# Init and start BLE, then run asyncio event loop for BLE only
_ble = ubluetooth.BLE()
_ble.active(True)
_ble.irq(_ble_irq)
print("BLE ready")

asyncio.run(main())
