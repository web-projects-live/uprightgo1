"""
Upright GO 1 — Web Dashboard + BLE Backend

SAFETY: Only reads from aaca (angle/notify) and writes 0x00/0x01 to aad3
(vibration). No other characteristics are touched. See slouch_buzzer.py
and the README for full safety notes.

Credits: BLE protocol reverse-engineered by
  https://github.com/niltonheck/upright-go-1-reverse-engineering
"""

import asyncio
import json
import socket
import sqlite3
import threading
import time
from datetime import date, timedelta

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from flask import Flask, Response, jsonify, render_template, request

DEVICE_NAME    = "UprightGO"
ANGLE_CHAR     = "0000aaca-0000-1000-8000-00805f9b34fb"  # notify/read — angle
VIBRATION_CHAR = "0000aad3-0000-1000-8000-00805f9b34fb"  # write — motor
DB_PATH        = "posture.db"
POLL_INTERVAL  = 0.15

state = {
    "connected":     False,
    "posture":       "unknown",
    "slouch_count":  0,
    "good_seconds":  0.0,
    "total_seconds": 0.0,
    "buzz_cooldown": 10.0,
}
_lock        = threading.Lock()
_slouch_flag = False

app = Flask(__name__)


# ── Database ──────────────────────────────────────────────────────────────────

def _db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            date             TEXT    NOT NULL,
            duration_seconds REAL    NOT NULL,
            good_seconds     REAL    NOT NULL,
            slouch_count     INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('buzz_cooldown','10.0')")
        c.commit()


def save_session():
    with _lock:
        dur   = state["total_seconds"]
        good  = state["good_seconds"]
        count = state["slouch_count"]
    if dur < 30:
        return
    with _db() as c:
        c.execute(
            "INSERT INTO sessions (date,duration_seconds,good_seconds,slouch_count)"
            " VALUES (?,?,?,?)",
            (date.today().isoformat(), dur, good, count),
        )
        c.commit()


def get_history(days=7):
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    with _db() as c:
        rows = c.execute(
            "SELECT date, SUM(duration_seconds), SUM(good_seconds), SUM(slouch_count)"
            " FROM sessions WHERE date >= ? GROUP BY date ORDER BY date DESC",
            (cutoff,),
        ).fetchall()
    return [
        {
            "date":             r[0],
            "duration_minutes": round(r[1] / 60, 1),
            "score":            round(r[2] / r[1] * 100 if r[1] > 0 else 0, 1),
            "slouch_count":     r[3],
        }
        for r in rows
    ]


def get_all_time():
    with _db() as c:
        row = c.execute(
            "SELECT COUNT(DISTINCT date), SUM(duration_seconds), AVG(good_seconds*1.0/duration_seconds)*100"
            " FROM sessions WHERE duration_seconds > 30"
        ).fetchone()
    return {
        "days":          row[0] or 0,
        "total_hours":   round((row[1] or 0) / 3600, 1),
        "avg_score":     round(row[2] or 0, 1),
    }


# ── BLE ──────────────────────────────────────────────────────────────────────

def _posture_handler(sender, data):
    global _slouch_flag
    if data and data[-1] == 0x02:
        _slouch_flag = True


async def _buzz(client):
    await client.write_gatt_char(VIBRATION_CHAR, bytes([0x01]), response=True)
    await asyncio.sleep(0.5)
    await client.write_gatt_char(VIBRATION_CHAR, bytes([0x00]), response=True)


async def _session(client):
    global _slouch_flag

    use_notify = False
    try:
        await client.start_notify(ANGLE_CHAR, _posture_handler)
        use_notify = True
    except BleakError:
        pass

    with _lock:
        state["connected"]     = True
        state["total_seconds"] = 0.0
        state["good_seconds"]  = 0.0
        state["slouch_count"]  = 0

    loop      = asyncio.get_running_loop()
    last_buzz = loop.time() - state["buzz_cooldown"]

    try:
        while client.is_connected:
            await asyncio.sleep(POLL_INTERVAL)

            if not use_notify:
                try:
                    data = await client.read_gatt_char(ANGLE_CHAR)
                    if data and data[-1] == 0x02:
                        _slouch_flag = True
                except BleakError:
                    break

            slouching    = _slouch_flag
            _slouch_flag = False

            with _lock:
                prev = state["posture"]
                state["posture"]        = "slouching" if slouching else "good"
                state["total_seconds"] += POLL_INTERVAL
                if not slouching:
                    state["good_seconds"] += POLL_INTERVAL
                elif prev != "slouching":
                    state["slouch_count"] += 1
                cooldown = state["buzz_cooldown"]

            if slouching:
                now = loop.time()
                if now - last_buzz >= cooldown:
                    last_buzz = now
                    try:
                        await _buzz(client)
                    except BleakError:
                        break
    finally:
        with _lock:
            state["connected"] = False
            state["posture"]   = "unknown"
        if use_notify:
            try:
                await client.stop_notify(ANGLE_CHAR)
            except Exception:
                pass
        try:
            await client.write_gatt_char(VIBRATION_CHAR, bytes([0x00]), response=True)
        except Exception:
            pass
        save_session()


async def _ble_loop():
    while True:
        try:
            print("Scanning for UprightGO...")
            device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)
            if not device:
                print("Device not found — retrying in 5s...")
                await asyncio.sleep(5)
                continue
            print(f"Found {device.address} — connecting...")
            async with BleakClient(device.address, timeout=20.0) as client:
                if client.is_connected:
                    print("Connected!")
                    await _session(client)
        except (BleakError, TimeoutError, OSError) as e:
            print(f"BLE error: {e}")
        await asyncio.sleep(5)


def _start_ble():
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ble_loop())
    threading.Thread(target=run, daemon=True).start()


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


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
        })


@app.route("/api/history")
def api_history():
    return jsonify(get_history())


@app.route("/api/alltime")
def api_alltime():
    return jsonify(get_all_time())


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        cooldown = max(3.0, min(60.0, float(request.get_json().get("buzz_cooldown", 10))))
        with _lock:
            state["buzz_cooldown"] = cooldown
        with _db() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES ('buzz_cooldown',?)", (str(cooldown),))
            c.commit()
        return jsonify({"ok": True, "buzz_cooldown": cooldown})
    with _db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='buzz_cooldown'").fetchone()
    return jsonify({"buzz_cooldown": float(row[0]) if row else 10.0})


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
                }
            yield f"data: {json.dumps(data)}\n\n"
            time.sleep(0.5)
    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    init_db()
    with _db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='buzz_cooldown'").fetchone()
        if row:
            state["buzz_cooldown"] = float(row[0])

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
