"""
Upright GO 1 — Web Dashboard v2

SAFETY: Only reads from aaca (angle/notify) and writes 0x00/0x01 to aad3
(vibration). No other characteristics are touched. See README for safety notes.
Credits: BLE protocol by niltonheck/upright-go-1-reverse-engineering
"""

import asyncio
import json
import platform
import random
import socket
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta

from flask import Flask, Response, jsonify, render_template, request

# bleak doesn't support Android — skip BLE gracefully
_BLE_SUPPORTED = not (sys.platform == "linux" and "android" in platform.release().lower())
if _BLE_SUPPORTED:
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError

DEVICE_NAME    = "UprightGO"
ANGLE_CHAR     = "0000aaca-0000-1000-8000-00805f9b34fb"
VIBRATION_CHAR = "0000aad3-0000-1000-8000-00805f9b34fb"
DB_PATH        = "posture.db"
POLL_INTERVAL  = 0.15   # seconds between BLE reads

# Sensitivity presets: alert only triggers when slouch_ratio >= threshold
# across a sliding window of window_s seconds. Eliminates walking/standing false positives.
PRESETS = {
    "lenient": {"window_s": 8.0,  "threshold": 0.70, "cooldown": 30.0},
    "normal":  {"window_s": 5.0,  "threshold": 0.60, "cooldown": 15.0},
    "strict":  {"window_s": 2.5,  "threshold": 0.50, "cooldown":  8.0},
}

state = {
    "connected":      False,
    "posture":        "unknown",
    "slouch_count":   0,
    "good_seconds":   0.0,
    "total_seconds":  0.0,
    "active_profile": None,   # {"id", "name", "settings"}
    "mode":           "desk", # desk | moving | break
    "slouch_ratio":   0.0,    # 0-1, current window ratio shown in UI pressure bar
}
_lock          = threading.Lock()
_raw_slouch    = False
_slouch_samples: list = []
_clear_window  = False   # set True when mode/profile changes


# ── Database ──────────────────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profile_settings (
                profile_id       INTEGER PRIMARY KEY REFERENCES profiles(id),
                sensitivity      TEXT    DEFAULT 'normal',
                buzz_cooldown    REAL    DEFAULT 15.0,
                slouch_window_s  REAL    DEFAULT 5.0,
                slouch_threshold REAL    DEFAULT 0.60,
                daily_goal_min   INTEGER DEFAULT 20
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id       INTEGER NOT NULL DEFAULT 1,
                date             TEXT    NOT NULL,
                duration_seconds REAL    NOT NULL,
                good_seconds     REAL    NOT NULL,
                slouch_count     INTEGER NOT NULL
            );
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
        if "profile_id" not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1")
        c.commit()


def _default_settings(pid):
    return {"profile_id": pid, "sensitivity": "normal", "buzz_cooldown": 15.0,
            "slouch_window_s": 5.0, "slouch_threshold": 0.60, "daily_goal_min": 20}


def get_profiles():
    with _db() as c:
        rows = c.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        result = []
        for p in rows:
            s = c.execute("SELECT * FROM profile_settings WHERE profile_id=?", (p["id"],)).fetchone()
            result.append({"id": p["id"], "name": p["name"], "created_at": p["created_at"],
                           "settings": dict(s) if s else _default_settings(p["id"])})
        return result


def create_profile(name: str) -> int:
    with _db() as c:
        cur = c.execute("INSERT INTO profiles (name, created_at) VALUES (?,?)",
                        (name.strip(), date.today().isoformat()))
        pid = cur.lastrowid
        c.execute("INSERT OR IGNORE INTO profile_settings (profile_id) VALUES (?)", (pid,))
        c.commit()
    return pid


def update_settings(pid: int, data: dict) -> dict:
    sens = data.get("sensitivity", "normal")
    p    = PRESETS.get(sens, PRESETS["normal"]) if sens in PRESETS else PRESETS["normal"]
    cd   = max(3.0,  min(120.0, float(data.get("buzz_cooldown",    p["cooldown"]))))
    ws   = max(1.0,  min(15.0,  float(data.get("slouch_window_s",  p["window_s"]))))
    th   = max(0.30, min(0.90,  float(data.get("slouch_threshold", p["threshold"]))))
    gm   = max(5,    min(120,   int(  data.get("daily_goal_min",   20))))
    if sens not in PRESETS:
        sens = "custom"
    with _db() as c:
        c.execute(
            "INSERT OR REPLACE INTO profile_settings"
            " (profile_id,sensitivity,buzz_cooldown,slouch_window_s,slouch_threshold,daily_goal_min)"
            " VALUES (?,?,?,?,?,?)", (pid, sens, cd, ws, th, gm))
        c.commit()
    return {"profile_id": pid, "sensitivity": sens, "buzz_cooldown": cd,
            "slouch_window_s": ws, "slouch_threshold": th, "daily_goal_min": gm}


def _save_session(pid, total_s, good_s, count):
    if total_s < 30 or pid is None:
        return
    with _db() as c:
        c.execute(
            "INSERT INTO sessions (profile_id,date,duration_seconds,good_seconds,slouch_count)"
            " VALUES (?,?,?,?,?)",
            (pid, date.today().isoformat(), total_s, good_s, count))
        c.commit()


def get_history(pid, days=7):
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    with _db() as c:
        rows = c.execute(
            "SELECT date, SUM(duration_seconds) d, SUM(good_seconds) g, SUM(slouch_count) s"
            " FROM sessions WHERE profile_id=? AND date>=? GROUP BY date ORDER BY date DESC",
            (pid, cutoff)).fetchall()
    return [{"date": r["date"],
             "duration_minutes": round(r["d"] / 60, 1),
             "score": round(r["g"] / r["d"] * 100 if r["d"] > 0 else 0, 1),
             "slouch_count": r["s"]} for r in rows]


def get_alltime(pid):
    with _db() as c:
        r = c.execute(
            "SELECT COUNT(DISTINCT date), SUM(duration_seconds),"
            " AVG(good_seconds*1.0/NULLIF(duration_seconds,0))*100"
            " FROM sessions WHERE profile_id=? AND duration_seconds>30", (pid,)).fetchone()
    return {"days": r[0] or 0, "total_hours": round((r[1] or 0) / 3600, 1),
            "avg_score": round(r[2] or 0, 1)}


def get_coaching(pid, daily_goal_min=20):
    TIPS = {
        "beginner": [
            "Start with 15-20 minute sessions — consistency matters more than duration.",
            "Calibrate before each session: sit tall, double-press the button.",
            "Tip: look at your monitor straight-on to naturally encourage upright posture.",
        ],
        "intermediate": [
            f"Try extending to {daily_goal_min}+ minutes today to hit your daily goal.",
            "Notice which tasks cause the most slouching — meetings? typing? phone?",
            "Your body is adapting. Each session makes upright feel more natural.",
        ],
        "advanced": [
            "You're close to graduation! Aim for 75%+ score for 7 qualifying days.",
            "Try the guided exercises to build the muscles that hold your posture.",
            "Almost there — strong posture habits take about 6 weeks to fully form.",
        ],
        "graduated": [
            "Graduated! Wear 2-3x per week to maintain the habit.",
            "Your posture is trained. Try the Strict sensitivity to keep sharp.",
            "Congratulations — great posture reduces back pain and boosts energy.",
        ],
    }

    with _db() as c:
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        rows = c.execute(
            "SELECT date, SUM(duration_seconds) d, SUM(good_seconds) g"
            " FROM sessions WHERE profile_id=? AND date>=?"
            " GROUP BY date ORDER BY date DESC", (pid, cutoff)).fetchall()
        best = c.execute(
            "SELECT MAX(good_seconds*1.0/NULLIF(duration_seconds,0))*100"
            " FROM sessions WHERE profile_id=? AND duration_seconds>60", (pid,)).fetchone()
        today_row = c.execute(
            "SELECT SUM(duration_seconds) FROM sessions WHERE profile_id=? AND date=?",
            (pid, date.today().isoformat())).fetchone()

    best_score    = round(best[0] or 0, 1)
    today_minutes = round((today_row[0] or 0) / 60, 1)
    total_days    = len(rows)

    if not rows:
        return {"phase": "beginner", "streak_days": 0, "seven_day_avg": 0.0,
                "trend": "stable", "graduation_days": 0, "graduation_target": 7,
                "graduated": False, "best_score": 0, "total_days": 0,
                "today_minutes": today_minutes, "daily_goal_min": daily_goal_min,
                "tip": TIPS["beginner"][0]}

    by_date = {r["date"]: r for r in rows}

    # Streak
    streak, d = 0, date.today()
    while d.isoformat() in by_date:
        streak += 1
        d -= timedelta(days=1)

    # 7-day avg + qualifying days for graduation
    seven_scores, qual_days = [], 0
    for i in range(7):
        key = (date.today() - timedelta(days=i)).isoformat()
        if key in by_date:
            r     = by_date[key]
            score = r["g"] / r["d"] * 100 if r["d"] > 0 else 0
            seven_scores.append(score)
            if score >= 75 and r["d"] / 60 >= daily_goal_min:
                qual_days += 1
    seven_avg = round(sum(seven_scores) / len(seven_scores), 1) if seven_scores else 0.0

    # Trend: this week vs last week
    def week_avg(start, end):
        scores = []
        for i in range(start, end):
            key = (date.today() - timedelta(days=i)).isoformat()
            if key in by_date:
                r = by_date[key]
                if r["d"] > 0:
                    scores.append(r["g"] / r["d"] * 100)
        return sum(scores) / len(scores) if scores else None

    a1, a2 = week_avg(1, 8), week_avg(8, 15)
    if a1 is not None and a2 is not None:
        diff  = a1 - a2
        trend = "improving" if diff > 5 else "declining" if diff < -5 else "stable"
    else:
        trend = "stable"

    graduated = qual_days >= 7
    phase     = ("graduated" if graduated
                 else "advanced"     if total_days >= 22
                 else "intermediate" if total_days >= 7
                 else "beginner")

    return {"phase": phase, "streak_days": streak, "seven_day_avg": seven_avg,
            "trend": trend, "graduation_days": qual_days, "graduation_target": 7,
            "graduated": graduated, "best_score": best_score, "total_days": total_days,
            "today_minutes": today_minutes, "daily_goal_min": daily_goal_min,
            "tip": random.choice(TIPS[phase])}


def _load_first_profile():
    profiles = get_profiles()
    if not profiles:
        return None
    p = profiles[0]
    return {"id": p["id"], "name": p["name"], "settings": p["settings"]}


# ── BLE ──────────────────────────────────────────────────────────────────────

def _posture_cb(sender, data):
    global _raw_slouch
    if data and data[-1] == 0x02:
        _raw_slouch = True


async def _buzz(client):
    await client.write_gatt_char(VIBRATION_CHAR, bytes([0x01]), response=True)
    await asyncio.sleep(0.5)
    await client.write_gatt_char(VIBRATION_CHAR, bytes([0x00]), response=True)


async def _session(client):
    global _raw_slouch, _slouch_samples, _clear_window

    with _lock:
        profile  = state["active_profile"]
    cfg       = profile["settings"] if profile else _default_settings(0)
    pid       = profile["id"]       if profile else None
    window_s  = float(cfg.get("slouch_window_s",  5.0))
    threshold = float(cfg.get("slouch_threshold", 0.60))
    cooldown  = float(cfg.get("buzz_cooldown",   15.0))
    max_polls = max(3, round(window_s / POLL_INTERVAL))

    _slouch_samples = []
    _raw_slouch     = False
    _clear_window   = False

    use_notify = False
    try:
        await client.start_notify(ANGLE_CHAR, _posture_cb)
        use_notify = True
    except BleakError:
        pass

    with _lock:
        state.update(connected=True, total_seconds=0.0, good_seconds=0.0,
                     slouch_count=0, posture="unknown")

    loop      = asyncio.get_running_loop()
    last_buzz = loop.time() - cooldown

    try:
        while client.is_connected:
            await asyncio.sleep(POLL_INTERVAL)

            if _clear_window:
                _clear_window   = False
                _slouch_samples = []

            if not use_notify:
                try:
                    data = await client.read_gatt_char(ANGLE_CHAR)
                    if data and data[-1] == 0x02:
                        _raw_slouch = True
                except BleakError:
                    break

            raw         = _raw_slouch
            _raw_slouch = False

            _slouch_samples.append(1 if raw else 0)
            if len(_slouch_samples) > max_polls * 2:
                _slouch_samples = _slouch_samples[-max_polls:]

            recent       = _slouch_samples[-max_polls:]
            ratio        = sum(recent) / len(recent) if recent else 0.0
            window_ready = len(recent) >= max(3, max_polls // 3)
            is_slouching = window_ready and ratio >= threshold

            with _lock:
                mode = state["mode"]
                prev = state["posture"]
                state["posture"]       = "slouching" if is_slouching else ("good" if window_ready else "unknown")
                state["slouch_ratio"]  = round(ratio, 3)
                state["total_seconds"] += POLL_INTERVAL
                if not is_slouching and window_ready:
                    state["good_seconds"] += POLL_INTERVAL
                if is_slouching and prev != "slouching":
                    state["slouch_count"] += 1

            if is_slouching and mode == "desk":
                now = loop.time()
                if now - last_buzz >= cooldown:
                    last_buzz = now
                    try:
                        await _buzz(client)
                    except BleakError:
                        break
    finally:
        with _lock:
            total_s = state["total_seconds"]
            good_s  = state["good_seconds"]
            count   = state["slouch_count"]
            state.update(connected=False, posture="unknown", slouch_ratio=0.0)
        if use_notify:
            try: await client.stop_notify(ANGLE_CHAR)
            except Exception: pass
        try: await client.write_gatt_char(VIBRATION_CHAR, bytes([0x00]), response=True)
        except Exception: pass
        _save_session(pid, total_s, good_s, count)
        _slouch_samples.clear()


async def _ble_loop():
    while True:
        try:
            print("Scanning for UprightGO...")
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            if not device:
                print("Not found, retrying in 5s...")
                await asyncio.sleep(5)
                continue
            print(f"Found {device.address}, connecting...")
            async with BleakClient(device.address, timeout=20.0) as client:
                if client.is_connected:
                    print("Connected!")
                    await _session(client)
        except (BleakError, TimeoutError, OSError) as e:
            print(f"BLE: {e}")
        await asyncio.sleep(5)


def _start_ble():
    if not _BLE_SUPPORTED:
        print("BLE: not supported on this platform — running in dashboard-only mode")
        return
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ble_loop())
    threading.Thread(target=run, daemon=True).start()


# ── Flask ─────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/profiles", methods=["GET", "POST"])
def api_profiles():
    if request.method == "POST":
        name = (request.get_json() or {}).get("name", "").strip()
        if not name:
            return jsonify({"error": "Name required"}), 400
        pid  = create_profile(name)
        with _lock:
            if state["active_profile"] is None:
                state["active_profile"] = {"id": pid, "name": name,
                                            "settings": _default_settings(pid)}
        return jsonify(next(p for p in get_profiles() if p["id"] == pid))
    return jsonify(get_profiles())


@app.route("/api/profiles/<int:pid>/activate", methods=["POST"])
def api_activate(pid):
    profiles = get_profiles()
    target   = next((p for p in profiles if p["id"] == pid), None)
    if not target:
        return jsonify({"error": "Not found"}), 404
    with _lock:
        old_pid = (state["active_profile"] or {}).get("id")
        ts, gs, cnt = state["total_seconds"], state["good_seconds"], state["slouch_count"]
        state["active_profile"] = {"id": target["id"], "name": target["name"],
                                    "settings": target["settings"]}
        state.update(total_seconds=0.0, good_seconds=0.0, slouch_count=0,
                     posture="unknown", slouch_ratio=0.0)
    if old_pid:
        _save_session(old_pid, ts, gs, cnt)
    global _clear_window
    _clear_window = True
    return jsonify({"ok": True, "active": target["id"]})


@app.route("/api/profiles/<int:pid>/settings", methods=["GET", "PUT"])
def api_profile_settings(pid):
    if request.method == "PUT":
        s = update_settings(pid, request.get_json() or {})
        with _lock:
            if (state["active_profile"] or {}).get("id") == pid:
                state["active_profile"]["settings"] = s
                global _clear_window
                _clear_window = True
        return jsonify(s)
    with _db() as c:
        s = c.execute("SELECT * FROM profile_settings WHERE profile_id=?", (pid,)).fetchone()
    return jsonify(dict(s) if s else _default_settings(pid))


@app.route("/api/mode", methods=["POST"])
def api_mode():
    global _clear_window
    mode = (request.get_json() or {}).get("mode", "desk")
    if mode not in ("desk", "moving", "break"):
        return jsonify({"error": "Invalid mode"}), 400
    with _lock:
        old, state["mode"] = state["mode"], mode
    if mode == "desk" and old != "desk":
        _clear_window = True
    return jsonify({"ok": True, "mode": mode})


@app.route("/api/history")
def api_history():
    with _lock:
        pid = (state["active_profile"] or {}).get("id")
    return jsonify(get_history(pid) if pid else [])


@app.route("/api/alltime")
def api_alltime():
    with _lock:
        pid = (state["active_profile"] or {}).get("id")
    return jsonify(get_alltime(pid) if pid else {"days": 0, "total_hours": 0, "avg_score": 0})


@app.route("/api/coaching")
def api_coaching():
    with _lock:
        profile = state["active_profile"]
    if not profile:
        return jsonify({"error": "No profile"}), 400
    goal = int(profile["settings"].get("daily_goal_min", 20))
    return jsonify(get_coaching(profile["id"], goal))


@app.route("/api/status")
def api_status():
    with _lock:
        tot = state["total_seconds"]
        return jsonify({
            "connected":     state["connected"],
            "posture":       state["posture"],
            "slouch_count":  state["slouch_count"],
            "score":         round(state["good_seconds"] / tot * 100 if tot else 0),
            "total_minutes": round(tot / 60, 1),
            "mode":          state["mode"],
            "slouch_ratio":  state["slouch_ratio"],
            "profile":       state["active_profile"],
        })


@app.route("/api/stream")
def api_stream():
    def generate():
        while True:
            with _lock:
                tot  = state["total_seconds"]
                data = {
                    "connected":     state["connected"],
                    "posture":       state["posture"],
                    "slouch_count":  state["slouch_count"],
                    "score":         round(state["good_seconds"] / tot * 100 if tot else 0),
                    "total_minutes": round(tot / 60, 1),
                    "mode":          state["mode"],
                    "slouch_ratio":  state["slouch_ratio"],
                    "profile_name":  (state["active_profile"] or {}).get("name", ""),
                }
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(0.5)
    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    init_db()
    with _lock:
        state["active_profile"] = _load_first_profile()
    _start_ble()
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "?.?.?.?"
    print("\n-----------------------------------------")
    print(f"  Dashboard : http://localhost:5000")
    print(f"  On phone  : http://{local_ip}:5000  (same Wi-Fi)")
    print("-----------------------------------------\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
