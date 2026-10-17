"""
tuya_keys.py — fetch your Tuya / Smart Life bulbs' local keys into devices.json.

Logs in with a QR code from the Smart Life app, so no Tuya developer account
is needed:

    pip install tinytuya tuya-device-sharing-sdk segno
    python tuya_keys.py <user code>

Find the user code in Smart Life: Me → ⚙ Settings → Account and Security →
User Code.  Scan the QR code this prints with the app's scan button and
approve.  The approval screen says "Home Assistant": this uses the same Smart
Life login as Home Assistant's built-in Tuya integration.

It then finds each device's IP and protocol version on your network and writes
devices.json next to this script, where master.py, music_lights.py and
audio_lights.py pick it up.  devices.json holds the keys — it's gitignored.
"""

import json
import os
import sys
import time

import segno
from tinytuya import scanner
from tuya_sharing import LoginControl, Manager

from tuya_light import DEVICES_FILE

# Home Assistant's Smart Life login (homeassistant/components/tuya/const.py)
CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA    = "haauthorize"

LOGIN_TIMEOUT = 300   # seconds to wait for the scan


def login(user_code: str) -> dict:
    """Show a QR code, wait for the Smart Life app to approve it, return the token info."""
    control = LoginControl()
    resp = control.qr_code(CLIENT_ID, SCHEMA, user_code)
    if not resp.get("success"):
        sys.exit(f"Smart Life rejected the user code: {resp.get('msg') or resp}")
    token = resp["result"]["qrcode"]

    qr  = segno.make_qr(f"tuyaSmart--qrLogin?token={token}")
    png = os.path.join(os.path.dirname(DEVICES_FILE), "tuya_login_qr.png")
    qr.save(png, scale=10, border=4)
    qr.terminal(compact=True)
    print(f"\nScan this with the Smart Life app (or open {png}), then approve.")

    try:
        deadline = time.time() + LOGIN_TIMEOUT
        while time.time() < deadline:
            ok, info = control.login_result(token, CLIENT_ID, user_code)
            if ok:
                return info
            time.sleep(2)
    finally:
        os.remove(png)
    sys.exit("Timed out waiting for the scan — run it again for a fresh QR code.")


def fetch_devices(user_code: str, info: dict) -> list:
    """Every device on the Smart Life account, with its local key."""
    token_info = {k: info[k] for k in ("t", "uid", "expire_time", "access_token", "refresh_token")}
    mgr = Manager(CLIENT_ID, user_code, info["terminal_id"], info["endpoint"], token_info, None)
    mgr.update_device_cache()
    return list(mgr.device_map.values())


def find_on_lan(ids: list[str]) -> dict[str, tuple[str, str]]:
    """Map device id → (ip, protocol version) from the devices' UDP broadcasts."""
    print("Looking for them on your network (up to ~20 s)...")
    found = scanner.devices(verbose=False, poll=False, byID=True, wantids=ids,
                            color=False, assume_yes=True)
    return {dev_id: (d.get("ip", ""), str(d.get("version", ""))) for dev_id, d in found.items()}


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    user_code = sys.argv[1]

    info  = login(user_code)
    cloud = fetch_devices(user_code, info)
    if not cloud:
        sys.exit("No devices on this Smart Life account.")
    lan = find_on_lan([d.id for d in cloud])

    entries = []
    print()
    for d in cloud:
        ip, version = lan.get(d.id, ("", ""))
        entries.append({"name": d.name, "id": d.id, "key": d.local_key,
                        "category": d.category, "product_id": d.product_id,
                        "product_name": d.product_name, "ip": ip, "version": version})
        print(f"  {d.name:<28} {ip or 'not found on this network'}")

    with open(DEVICES_FILE, "w") as f:
        json.dump(entries, f, indent=2)
    os.chmod(DEVICES_FILE, 0o600)
    print(f"\nWrote {len(entries)} device(s) to {DEVICES_FILE}")


if __name__ == "__main__":
    main()
