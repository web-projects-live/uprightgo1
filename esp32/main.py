"""
Upright GO 1 — ESP32 MicroPython firmware
No asyncio — HTTP and BLE each run in their own thread.

First boot: connects to Wi-Fi if wifi.json exists, otherwise starts
"UprightGO-Setup" hotspot (no password) and serves a setup page at
http://192.168.4.1. After saving Wi-Fi it reboots automatically.

SAFETY: Only reads aaca (angle) and writes 0x00/0x01 to aad3 (vibration).
Credits: BLE protocol by niltonheck/upright-go-1-reverse-engineering
"""

import ubluetooth
import ujson
import network
import time
import struct
import gc
import _thread

# ── Constants ────────────────────────────────────────────────────────────────
DEVICE_NAME    = "UprightGO"
POLL_INTERVAL  = 0.15
HISTORY_FILE   = "history.json"
SETTINGS_FILE  = "settings.json"
WIFI_FILE      = "wifi.json"
MAX_HISTORY    = 30

DEFAULT_SETTINGS = {
    "sensitivity": "normal", "slouch_window_s": 5.0,
    "slouch_threshold": 0.60, "buzz_cooldown": 15.0,
    "daily_goal_min": 20, "slouch_angle": 10,
}
PRESETS = {
    "lenient": {"slouch_window_s": 8.0,  "slouch_threshold": 0.70, "buzz_cooldown": 30.0},
    "normal":  {"slouch_window_s": 5.0,  "slouch_threshold": 0.60, "buzz_cooldown": 15.0},
    "strict":  {"slouch_window_s": 2.5,  "slouch_threshold": 0.50, "buzz_cooldown":  8.0},
}

ANGLE_UUID     = ubluetooth.UUID("0000aaca-0000-1000-8000-00805f9b34fb")
VIBRATION_UUID = ubluetooth.UUID("0000aad3-0000-1000-8000-00805f9b34fb")

_IRQ_SCAN_RESULT            = 5
_IRQ_SCAN_DONE              = 6
_IRQ_PERIPHERAL_CONNECT     = 7
_IRQ_PERIPHERAL_DISCONNECT  = 8
_IRQ_GATTC_CHAR_RESULT      = 11
_IRQ_GATTC_CHAR_DONE        = 12
_IRQ_GATTC_READ_RESULT      = 15
_IRQ_GATTC_READ_DONE        = 16

# ── Shared state ──────────────────────────────────────────────────────────────
settings = {}
state = {
    "connected": False, "posture": "unknown", "slouch_count": 0,
    "good_seconds": 0.0, "total_seconds": 0.0,
    "mode": "desk", "slouch_ratio": 0.0, "score": 0,
}
_slouch_samples = []
_clear_window   = False
_last_buzz      = 0.0

_ble              = None
_ble_state        = "idle"
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

def save_history(h):
    with open(HISTORY_FILE, "w") as f:
        ujson.dump(h[-MAX_HISTORY:], f)

def record_session():
    if state["total_seconds"] < 30:
        return
    h = load_history()
    tot = state["total_seconds"]
    t = time.localtime()
    h.append({
        "date": "{:04d}-{:02d}-{:02d}".format(t[0], t[1], t[2]),
        "score": int(state["good_seconds"] / tot * 100) if tot else 0,
        "duration_minutes": round(tot / 60, 1),
        "slouch_count": state["slouch_count"],
    })
    save_history(h)

def get_coaching():
    h = load_history()
    goal = settings["daily_goal_min"]
    if not h:
        return {"phase": "beginner", "streak_days": 0, "seven_day_avg": 0,
                "best_score": 0, "trend": "flat", "graduation_days": 0,
                "today_minutes": round(state["total_seconds"]/60,1),
                "daily_goal_min": goal, "tip": "Start a session to begin tracking!"}
    scores = [x["score"] for x in h[-7:]]
    avg  = int(sum(scores)/len(scores)) if scores else 0
    best = max((x["score"] for x in h), default=0)
    trend = "flat"
    if len(scores) >= 4:
        m = len(scores)//2
        trend = "improving" if sum(scores[m:])/len(scores[m:]) - sum(scores[:m])/m > 5 else \
                "declining" if sum(scores[:m])/m - sum(scores[m:])/len(scores[m:]) > 5 else "flat"
    phase = "advanced" if avg>=80 else "intermediate" if avg>=60 else "beginner"
    grad = sum(1 for x in h[-30:] if x["score"]>=75 and x["duration_minutes"]>=goal)
    if grad >= 7: phase = "graduated"
    t = time.localtime()
    today = "{:04d}-{:02d}-{:02d}".format(t[0],t[1],t[2])
    tm = round(state["total_seconds"]/60,1) + sum(x["duration_minutes"] for x in h if x["date"]==today)
    tips = ["Sit with your back against the chair back.",
            "Stand up every 30 minutes.", "Keep monitor at eye level.",
            "Roll shoulders back and down.", "Consistency beats perfection.",
            "Chin tucks strengthen the deep neck flexors.",
            "Wall angels mobilise the thoracic spine."]
    return {"phase": phase, "streak_days": min(len(h),7), "seven_day_avg": avg,
            "best_score": best, "trend": trend, "graduation_days": min(grad,7),
            "today_minutes": round(tm,1), "daily_goal_min": goal, "tip": tips[len(h)%len(tips)]}

def get_alltime():
    h = load_history()
    days = len(h)
    return {"days": days, "total_hours": round(sum(x["duration_minutes"] for x in h)/60,1),
            "avg_score": int(sum(x["score"] for x in h)/days) if days else 0}

# ── BLE IRQ ───────────────────────────────────────────────────────────────────
def _adv_name(adv):
    i = 0
    while i < len(adv):
        if i+1 >= len(adv): break
        n = adv[i]
        if n == 0: break
        if adv[i+1] in (0x08, 0x09):
            try: return adv[i+2:i+1+n].decode("utf-8")
            except: pass
        i += 1+n
    return None

def _ble_irq(event, data):
    global _ble_state, _conn_handle, _found_addr, _found_addr_type
    global _angle_handle, _vibration_handle, _chars, _pending_read, _read_result

    if event == _IRQ_SCAN_RESULT:
        addr_type, addr, adv_type, rssi, adv_data = data
        name = _adv_name(bytes(adv_data))
        if name and DEVICE_NAME in name:
            print("Found", name)
            _found_addr_type = addr_type
            _found_addr = bytes(addr)
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
        _ble_state = "discovering"
        _chars = {}
        print("Connected, discovering...")
        _ble.gattc_discover_characteristics(_conn_handle, 0x0001, 0xFFFF)

    elif event == _IRQ_PERIPHERAL_DISCONNECT:
        _conn_handle = _angle_handle = _vibration_handle = None
        _ble_state = "idle"
        _chars = {}
        state["connected"] = False
        state["posture"] = "unknown"
        print("Disconnected")

    elif event == _IRQ_GATTC_CHAR_RESULT:
        conn_handle, def_handle, value_handle, properties, uuid = data
        _chars[str(uuid).lower()] = value_handle

    elif event == _IRQ_GATTC_CHAR_DONE:
        ak = str(ANGLE_UUID).lower()
        vk = str(VIBRATION_UUID).lower()
        if ak in _chars and vk in _chars:
            _angle_handle = _chars[ak]
            _vibration_handle = _chars[vk]
            _ble_state = "ready"
            state["connected"] = True
            print("Ready! angle={} vib={}".format(_angle_handle, _vibration_handle))
        else:
            print("Chars not found, retrying")
            _ble_state = "idle"

    elif event == _IRQ_GATTC_READ_RESULT:
        conn_handle, value_handle, char_data = data
        _read_result = bytes(char_data)
        _pending_read = False

    elif event == _IRQ_GATTC_READ_DONE:
        conn_handle, value_handle, status = data
        if status != 0:
            _pending_read = False

# ── Posture ───────────────────────────────────────────────────────────────────
def _process_angle(data):
    global _last_buzz, _slouch_samples, _clear_window
    if _clear_window:
        _slouch_samples.clear()
        _clear_window = False
    angle = struct.unpack_from(">h", data, 0)[0] / 100.0
    raw   = angle > settings.get("slouch_angle", 10)
    ws    = settings["slouch_window_s"]
    thr   = settings["slouch_threshold"]
    mp    = max(1, int(ws / POLL_INTERVAL))
    _slouch_samples.append(1 if raw else 0)
    if len(_slouch_samples) > mp * 2:
        _slouch_samples = _slouch_samples[-mp:]
    recent = _slouch_samples[-mp:]
    ratio  = sum(recent)/len(recent) if recent else 0.0
    ready  = len(recent) >= max(3, mp//3)
    slouch = ready and ratio >= thr
    state["slouch_ratio"] = round(ratio, 3)
    state["posture"]      = "slouching" if slouch else "good"
    state["total_seconds"] += POLL_INTERVAL
    if not slouch:
        state["good_seconds"] += POLL_INTERVAL
    tot = state["total_seconds"]
    state["score"] = int(state["good_seconds"]/tot*100) if tot else 100
    now = time.time()
    if slouch and state["mode"]=="desk" and now-_last_buzz >= settings["buzz_cooldown"]:
        _last_buzz = now
        state["slouch_count"] += 1
        _buzz()

def _buzz():
    if _vibration_handle is None or _conn_handle is None:
        return
    try:
        _ble.gattc_write(_conn_handle, _vibration_handle, bytes([0x01]), 1)
        time.sleep(0.5)
        _ble.gattc_write(_conn_handle, _vibration_handle, bytes([0x00]), 1)
    except Exception as e:
        print("Buzz err:", e)

# ── BLE thread ────────────────────────────────────────────────────────────────
def ble_thread():
    global _ble, _ble_state, _found_addr, _pending_read, _read_result
    _ble = ubluetooth.BLE()
    _ble.active(True)
    _ble.irq(_ble_irq)
    print("BLE ready")
    while True:
        try:
            if _ble_state == "idle":
                _found_addr = None
                print("Scanning for UprightGO...")
                _ble_state = "scanning"
                _ble.gap_scan(10000, 30000, 30000)
                deadline = time.time() + 13
                while time.time() < deadline:
                    time.sleep(0.1)
                if _ble_state == "scanning":
                    _ble.gap_scan(None)
                    _ble_state = "idle"
                time.sleep(5)

            elif _ble_state == "ready":
                _pending_read = True
                _read_result  = None
                _ble.gattc_read(_conn_handle, _angle_handle)
                deadline = time.time() + 1
                while _pending_read and time.time() < deadline:
                    time.sleep(0.02)
                if _read_result and len(_read_result) >= 2:
                    _process_angle(_read_result)
                time.sleep(POLL_INTERVAL)

            else:
                time.sleep(0.5)

        except Exception as e:
            print("BLE err:", e)
            _ble_state = "idle"
            time.sleep(5)

# ── Wi-Fi setup portal ────────────────────────────────────────────────────────
def _load_wifi():
    try:
        with open(WIFI_FILE) as f:
            d = ujson.load(f)
            return d.get("ssid",""), d.get("password","")
    except Exception:
        pass
    try:
        import config as c
        if hasattr(c,"WIFI_SSID") and c.WIFI_SSID not in ("","YOUR_WIFI_SSID"):
            _save_wifi(c.WIFI_SSID, getattr(c,"WIFI_PASS",""))
            return c.WIFI_SSID, getattr(c,"WIFI_PASS","")
    except Exception:
        pass
    return "", ""

def _save_wifi(ssid, pw):
    with open(WIFI_FILE,"w") as f:
        ujson.dump({"ssid":ssid,"password":pw}, f)

def _url_decode(s):
    s = s.replace("+"," ")
    out, i = "", 0
    while i < len(s):
        if s[i]=="%" and i+2<len(s):
            try:
                out += chr(int(s[i+1:i+3],16)); i+=3; continue
            except: pass
        out += s[i]; i+=1
    return out

def _parse_form(body):
    p = {}
    try:
        for pair in body.decode().split("&"):
            if "=" in pair:
                k,v = pair.split("=",1)
                p[_url_decode(k)] = _url_decode(v)
    except: pass
    return p

_SETUP_PAGE = (b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
b"<!DOCTYPE html><html><head><meta charset=UTF-8>"
b"<meta name=viewport content='width=device-width,initial-scale=1'>"
b"<title>Upright GO Setup</title>"
b"<style>body{font-family:sans-serif;background:#1a1d2e;color:#e2e8f0;"
b"display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
b".c{background:#242840;border-radius:12px;padding:2rem;max-width:340px;width:100%}"
b"h2{margin:0 0 .5rem}p{color:#8892a4;margin:0 0 1.2rem;font-size:.9rem}"
b"input{width:100%;padding:.7rem;border-radius:8px;border:1px solid #363b5e;"
b"background:#1a1d2e;color:#e2e8f0;margin-bottom:.6rem;font-size:1rem;box-sizing:border-box}"
b"button{width:100%;padding:.8rem;border-radius:8px;border:none;cursor:pointer;"
b"font-size:1rem;font-weight:600;margin-bottom:.4rem}"
b".a{background:#7c6aff;color:#fff}.b{background:#363b5e;color:#8892a4}"
b"</style></head><body><div class=c>"
b"<h2>Upright GO 1</h2>"
b"<p>Enter your home Wi-Fi to connect, or skip to use the hotspot only.</p>"
b"<form method=POST action=/save>"
b"<input name=s placeholder='Wi-Fi name' autocomplete=off>"
b"<input name=p type=password placeholder='Wi-Fi password'>"
b"<button class=a>Save &amp; connect</button></form>"
b"<form method=POST action=/skip>"
b"<button class=b>Skip &mdash; hotspot only</button></form>"
b"</div></body></html>")

def run_setup_portal():
    import socket as sk
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="UprightGO-Setup", authmode=0)
    time.sleep(1)
    print("Setup portal at http://192.168.4.1")
    srv = sk.socket()
    srv.setsockopt(sk.SOL_SOCKET, sk.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(1)
    srv.settimeout(180)
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            print("Setup timed out")
            break
        try:
            conn.settimeout(5)
            req = b""
            while b"\r\n\r\n" not in req:
                c = conn.recv(256)
                if not c: break
                req += c
            line1 = req.split(b"\r\n")[0]
            parts = line1.split(b" ")
            method = parts[0] if parts else b""
            path   = parts[1].split(b"?")[0] if len(parts)>1 else b"/"
            if path == b"/save" and method == b"POST":
                cl = 0
                for ln in req.split(b"\r\n"):
                    if ln.lower().startswith(b"content-length:"):
                        try: cl = int(ln.split(b":")[1].strip())
                        except: pass
                body = req[req.find(b"\r\n\r\n")+4:]
                while len(body) < cl:
                    c = conn.recv(256)
                    if not c: break
                    body += c
                p = _parse_form(body)
                ssid = p.get("s","").strip()
                if ssid:
                    _save_wifi(ssid, p.get("p",""))
                    conn.send(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
                              b"<html><body style='font-family:sans-serif;background:#1a1d2e;"
                              b"color:#e2e8f0;padding:2rem'><h2>Saved!</h2>"
                              b"<p>Rebooting to connect...</p></body></html>")
                    conn.close()
                    srv.close()
                    time.sleep(2)
                    import machine; machine.reset()
                else:
                    conn.send(_SETUP_PAGE)
            elif path == b"/skip" and method == b"POST":
                _save_wifi("","")
                conn.send(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
                          b"<html><body style='font-family:sans-serif;background:#1a1d2e;"
                          b"color:#e2e8f0;padding:2rem'><h2>OK</h2>"
                          b"<p>Starting in hotspot mode...</p></body></html>")
                conn.close()
                srv.close()
                time.sleep(1)
                import machine; machine.reset()
            else:
                conn.send(_SETUP_PAGE)
        except Exception as e:
            print("Setup err:", e)
        finally:
            try: conn.close()
            except: pass

def connect_wifi():
    ssid, pw = _load_wifi()
    if ssid:
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            print("Connecting to Wi-Fi:", ssid)
            wlan.connect(ssid, pw)
            deadline = time.time() + 20
            while not wlan.isconnected() and time.time() < deadline:
                time.sleep(0.5)
        if wlan.isconnected():
            print("Wi-Fi OK:", wlan.ifconfig()[0])
            return
        print("Wi-Fi failed")
    run_setup_portal()

# ── HTTP server ───────────────────────────────────────────────────────────────
def _file_size(path):
    try:
        import uos; return uos.stat(path)[6]
    except: return -1

def _json(data):
    return ("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n"
            + ujson.dumps(data)).encode()

def _route(method, path, body):
    global _clear_window

    if path == "/api/status":
        tot = state["total_seconds"]
        return _json({"connected": state["connected"], "posture": state["posture"],
                      "slouch_count": state["slouch_count"], "score": state["score"],
                      "total_minutes": round(tot/60,1), "mode": state["mode"],
                      "slouch_ratio": state["slouch_ratio"]})

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
            d = ujson.loads(body) if body else {}
            p = d.get("sensitivity")
            if p and p in PRESETS:
                settings.update(PRESETS[p]); settings["sensitivity"] = p
            for k in ("slouch_window_s","slouch_threshold","buzz_cooldown",
                      "daily_goal_min","slouch_angle"):
                if k in d: settings[k] = d[k]
            save_settings_file(); _clear_window = True
            return _json({"ok": True})
        except Exception as e:
            return _json({"error": str(e)})

    if path == "/api/mode" and method == "POST":
        try:
            d = ujson.loads(body) if body else {}
            m = d.get("mode","desk")
            if m in ("desk","moving","break"):
                state["mode"] = m
                if m == "desk": _clear_window = True
            return _json({"ok": True})
        except Exception as e:
            return _json({"error": str(e)})

    if path == "/api/wifi-status":
        wlan = network.WLAN(network.STA_IF)
        ssid, _ = _load_wifi()
        return _json({"ssid": ssid, "connected": wlan.isconnected(),
                      "ip": wlan.ifconfig()[0] if wlan.isconnected() else ""})

    if path == "/api/wifi-save" and method == "POST":
        try:
            d = ujson.loads(body) if body else {}
            _save_wifi(d.get("ssid",""), d.get("password",""))
            return _json({"ok": True, "note": "Reboot to apply"})
        except Exception as e:
            return _json({"error": str(e)})

    if path == "/debug":
        import uos
        return _json({"mem": gc.mem_free(), "ble": _ble_state,
                      "files": [(f, uos.stat(f)[6]) for f in uos.listdir("/")]})

    return b"HTTP/1.0 404 Not Found\r\n\r\nNot found"

_STATIC = {
    "/":           ("index.html","text/html"),
    "/index.html": ("index.html","text/html"),
    "/style.css":  ("style.css", "text/css"),
    "/app.js":     ("app.js",    "application/javascript"),
}

def _handle(conn):
    try:
        conn.settimeout(5)
        req = b""
        while b"\r\n\r\n" not in req:
            c = conn.recv(256)
            if not c: break
            req += c
            if len(req) > 8192: break
        line1 = req.split(b"\r\n")[0]
        parts = line1.split(b" ")
        if len(parts) < 2: return
        method = parts[0].decode()
        path   = parts[1].split(b"?")[0].decode()
        cl = 0
        for ln in req.split(b"\r\n"):
            if ln.lower().startswith(b"content-length:"):
                try: cl = int(ln.split(b":")[1].strip())
                except: pass
        body = req[req.find(b"\r\n\r\n")+4:]
        while len(body) < cl:
            c = conn.recv(256)
            if not c: break
            body += c

        if path in _STATIC:
            fname, ctype = _STATIC[path]
            sz = _file_size(fname)
            if sz < 0:
                conn.send(b"HTTP/1.0 404 Not Found\r\n\r\nNot found")
                return
            conn.send("HTTP/1.0 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\n\r\n".format(ctype,sz).encode())
            with open(fname,"rb") as f:
                while True:
                    chunk = f.read(512)
                    if not chunk: break
                    conn.send(chunk)
            return

        conn.send(_route(method, path, body))

    except Exception as e:
        print("HTTP err:", e)

def http_thread():
    import socket as sk
    _thread.stack_size(8192)
    srv = sk.socket()
    srv.setsockopt(sk.SOL_SOCKET, sk.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(2)
    print("HTTP server on port 80")
    while True:
        try:
            conn, _ = srv.accept()
            try:
                _handle(conn)
            finally:
                conn.close()
                gc.collect()
        except Exception as e:
            print("Accept err:", e)
            time.sleep(1)

# ── Boot ──────────────────────────────────────────────────────────────────────
load_settings()
connect_wifi()

_thread.stack_size(8192)
_thread.start_new_thread(http_thread, ())
_thread.start_new_thread(ble_thread, ())

print("Running — http server and BLE active")

# Main thread keeps running (threads die if main exits)
while True:
    time.sleep(300)
    if state["connected"] and state["total_seconds"] > 60:
        record_session()
        state["total_seconds"] = 0.0
        state["good_seconds"]  = 0.0
        state["slouch_count"]  = 0
