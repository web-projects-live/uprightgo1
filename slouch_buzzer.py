"""
Upright GO 1 — Slouch Buzzer

SAFETY NOTES — read before modifying this script:
  - The original reverse-engineering repo author bricked their device by writing
    to characteristic 'aaa6', which is NOT in the documented GATT table. The
    CC2540 chip on this device accepts firmware updates over BLE without
    integrity checks, so writing to unknown characteristics risks corruption.
  - This script intentionally touches ONLY two documented characteristics:
      aaca  (notify/read)  — posture angle data; NEVER written to
      aad3  (write only)   — vibration motor; only values 0x00 and 0x01
  - DO NOT add writes to any other characteristic UUID.
  - DO NOT call client.get_services() or enumerate the full service tree;
    on some CC2540 firmware builds this can trigger OAD (firmware update) mode.

Setup:
  pip install bleak
  python slouch_buzzer.py

Calibration: sit up straight and double-press the button on the device BEFORE
running this script to set your upright baseline.
"""

import asyncio
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

DEVICE_NAME    = "UprightGO"
ANGLE_CHAR     = "0000aaca-0000-1000-8000-00805f9b34fb"  # notify/read — angle data
VIBRATION_CHAR = "0000aad3-0000-1000-8000-00805f9b34fb"  # write       — motor control

BUZZ_DURATION  = 0.5    # seconds to buzz each time
BUZZ_COOLDOWN  = 10.0   # seconds minimum between buzzes
POLL_INTERVAL  = 0.15   # seconds between posture reads (polling mode)

_slouch_flag = False
_connected   = False


def _posture_handler(sender, data):
    """Notification callback — read-only, never writes anything."""
    global _slouch_flag
    if len(data) > 0 and data[-1] == 0x02:
        _slouch_flag = True


def _disconnected(client: BleakClient):
    global _connected
    _connected = False
    print("\nDevice disconnected — will reconnect...")


async def _safe_buzz(client: BleakClient) -> None:
    """Write vibration on then off. Only ever sends 0x01 and 0x00."""
    await client.write_gatt_char(VIBRATION_CHAR, bytes([0x01]), response=True)
    await asyncio.sleep(BUZZ_DURATION)
    await client.write_gatt_char(VIBRATION_CHAR, bytes([0x00]), response=True)


async def _run_session(client: BleakClient) -> None:
    """
    Single connected session. Tries notifications first; falls back to polling
    if the Windows BLE stack rejects the CCCD write (common before bonding).
    """
    global _slouch_flag, _connected
    _connected = True

    use_notify = False
    try:
        await client.start_notify(ANGLE_CHAR, _posture_handler)
        use_notify = True
        print("Using BLE notifications.")
    except BleakError as e:
        print(f"Notifications unavailable ({e}), falling back to polling.")

    print("Monitoring posture. Press Ctrl+C to stop.\n")

    last_buzz = -BUZZ_COOLDOWN
    loop = asyncio.get_running_loop()

    try:
        while _connected:
            await asyncio.sleep(POLL_INTERVAL)

            # In polling mode, read the characteristic directly each cycle
            if not use_notify:
                try:
                    data = await client.read_gatt_char(ANGLE_CHAR)
                    if len(data) > 0 and data[-1] == 0x02:
                        _slouch_flag = True
                except BleakError:
                    break  # device gone, let outer loop reconnect

            if _slouch_flag:
                _slouch_flag = False
                now = loop.time()
                if now - last_buzz >= BUZZ_COOLDOWN:
                    last_buzz = now
                    print("Slouch detected — buzzing!")
                    try:
                        await _safe_buzz(client)
                    except BleakError:
                        break  # device gone
    finally:
        if use_notify:
            try:
                await client.stop_notify(ANGLE_CHAR)
            except Exception:
                pass
        try:
            await client.write_gatt_char(VIBRATION_CHAR, bytes([0x00]), response=True)
        except Exception:
            pass


async def main():
    print("Scanning for UprightGO device — make sure it's on and nearby...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=10.0)

    if device is None:
        print(
            f"\nCould not find '{DEVICE_NAME}'.\n"
            "Troubleshooting:\n"
            "  1. Make sure the Upright GO 1 is charged and turned on.\n"
            "  2. Bring it within a few feet of your PC.\n"
            "  3. Disconnect it from your phone/the Upright app first.\n"
            "  4. You may need to pair it once via Windows Settings > Bluetooth.\n"
        )
        return

    print(f"Found device at {device.address}")
    print("Sit up straight and double-press the button to calibrate if you haven't already.\n")

    try:
        while True:
            print("Connecting...")
            try:
                async with BleakClient(
                    device.address,
                    disconnected_callback=_disconnected,
                    timeout=20.0,
                ) as client:
                    if not client.is_connected:
                        print("Connection failed, retrying in 3s...")
                        await asyncio.sleep(3)
                        continue

                    print("Connected!")
                    await _run_session(client)

            except (BleakError, TimeoutError) as e:
                print(f"Connection error: {e}")

            await asyncio.sleep(3)  # brief pause before reconnect attempt

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    asyncio.run(main())
