"""
tuya_light.py — Tuya / Smart Life Wi-Fi bulbs, driven like a CozyLifeDevice.

Tuya bulbs encrypt their local protocol with a per-device local_key that only
Tuya's cloud hands out.  Fetch it once with tuya_keys.py (QR login through the
Smart Life app) or tinytuya's wizard; either writes devices.json next to these
scripts with each device's id, local key and IP.  load_tuya_lights() reads it.
It contains secrets, so it is gitignored.

Usage:
    from tuya_light import load_tuya_lights
    for light in load_tuya_lights():
        light.stream_color(240, 80, 200)   # hue 0–360, sat 0–100, bri 0–255
"""

import json
import os
import socket
import struct
import time
from typing import Optional

try:
    import tinytuya
    TUYA_AVAILABLE = True
except ImportError:
    TUYA_AVAILABLE = False


DEVICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices.json")

# Tuya bulbs queue commands they can't keep up with, so lag builds up if they
# are sent faster than they can apply them (~8/s for A19 bulbs measured on
# v3.5).  The bulb answers every command with a status report ~100 ms later,
# so stream updates wait for that reply before sending the next one; updates
# arriving in between are dropped (the next one carries the latest colour).
# If a reply goes missing, sending resumes after this long.
REPLY_TIMEOUT = 0.5

_STATUS_CMD = 8


class TuyaLight:
    """Persistent local connection to one Tuya colour bulb."""

    def __init__(self, dev_id: str, ip: str, local_key: str,
                 version: float = 3.3, name: str = ""):
        if not TUYA_AVAILABLE:
            raise OSError("tinytuya not installed — pip install tinytuya")
        self.ip   = ip
        self.name = name or dev_id
        self._dev = tinytuya.BulbDevice(
            dev_id, ip, local_key, version=version, persist=True,
            connection_timeout=3, connection_retry_limit=1, connection_retry_delay=1,
        )
        self._dev.sendWait = None   # fire-and-forget sends shouldn't sleep
        self._on   = False
        self._mode: Optional[str] = None
        self._last_send = 0.0
        self._awaiting_reply = False
        self._rx = b""
        self._connect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        # status() opens the socket (and negotiates a session key on v3.4+),
        # and lets tinytuya work out which DPs this bulb uses.
        status = self._dev.status()
        if not status or "Error" in status:
            err = (status or {}).get("Error", "no response")
            self._dev.close()
            raise OSError(f"{self.ip}: {err}")
        if not self._dev.bulb_configured or not self._dev.dpset["colour"]:
            self._dev.close()
            raise OSError(f"{self.ip}: not a colour bulb (DPs {sorted(status['dps'])})")
        self._on   = bool(status["dps"].get(self._dev.dpset["switch"]))
        self._mode = status["dps"].get(self._dev.dpset["mode"])

    def reconnect(self) -> None:
        """Close and reopen the connection."""
        self.close()
        self._connect()

    def close(self) -> None:
        self._dev.close()
        self._awaiting_reply = False
        self._rx = b""

    @property
    def _sock(self) -> Optional[socket.socket]:
        return self._dev.socket

    def __enter__(self) -> "TuyaLight":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"TuyaLight(ip={self.ip}, name={self.name!r})"

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def _set(self, dps: dict) -> None:
        result = self._dev.set_multiple_values(dps, nowait=True)
        if result and "Error" in result:
            raise OSError(f"{self.ip}: {result['Error']}")
        self._last_send = time.monotonic()
        self._awaiting_reply = True
        self._drain()

    def _drain(self) -> None:
        """Read and discard what the bulb sent, noting any status reply.

        Unread replies would fill the kernel recv buffer until the bulb's
        sends stall (same as CozyLifeDevice._drain()).
        """
        sock = self._dev.socket
        if sock is None:
            return
        try:
            sock.setblocking(False)
            while True:
                data = sock.recv(4096)
                if not data:
                    # Bulb closed the connection (idle timeout); the next
                    # send opens a fresh one.
                    self.close()
                    return
                self._rx += data
        except OSError:
            pass
        sock.settimeout(self._dev.connection_timeout)
        if _STATUS_CMD in self._take_frames():
            self._awaiting_reply = False

    def _take_frames(self) -> list[int]:
        """Pop complete messages off self._rx and return their command codes.

        Only the cleartext headers are read — no decryption needed.
        """
        cmds = []
        while True:
            start = min((i for i in (self._rx.find(b"\x00\x00\x55\xaa"),
                                     self._rx.find(b"\x00\x00\x66\x99")) if i >= 0),
                        default=-1)
            if start < 0:
                self._rx = self._rx[-3:]   # keep a possible partial prefix
                return cmds
            buf = self._rx[start:]
            if buf[2:4] == b"\x55\xaa" and len(buf) >= 16:
                _, _, cmd, length = struct.unpack(">4I", buf[:16])
                total = 16 + length
            elif buf[2:4] == b"\x66\x99" and len(buf) >= 18:
                _, _, _, cmd, length = struct.unpack(">IHIII", buf[:18])
                total = 18 + length + 4
            else:
                total = None
            if total is not None and total > 4096:
                self._rx = buf[4:]         # not a real header; resync
                continue
            if total is None or len(buf) < total:
                self._rx = buf
                return cmds
            cmds.append(cmd)
            self._rx = buf[total:]

    def _value(self, fraction: float) -> int:
        """Scale 0.0–1.0 onto this bulb's brightness range (e.g. 10–1000)."""
        lo, hi = self._dev.dpset["value_min"], self._dev.dpset["value_max"]
        return round(lo + (hi - lo) * max(0.0, min(1.0, fraction)))

    # ------------------------------------------------------------------
    # Public API (mirrors CozyLifeDevice)
    # ------------------------------------------------------------------

    def turn_on(self) -> None:
        self._set({self._dev.dpset["switch"]: True})
        self._on = True

    def turn_off(self) -> None:
        # music_lights.py calls this every tick while paused; repeats would
        # just queue up in the bulb.
        if not self._on:
            return
        self._set({self._dev.dpset["switch"]: False})
        self._on = False

    def stream_color(self, hue: float, saturation: float, brightness: int) -> bool:
        """Fire-and-forget colour update for high-rate loops (music, disco).

        hue 0–360, saturation 0–100, brightness 0–255.  Returns False if the
        update was dropped because the bulb hasn't answered the previous one.
        """
        if self._awaiting_reply:
            self._drain()
            if self._awaiting_reply and time.monotonic() - self._last_send < REPLY_TIMEOUT:
                return False
        dp = self._dev.dpset
        colour = tinytuya.BulbDevice.hsv_to_hexvalue(
            (hue % 360) / 360,
            max(0.0, min(100.0, saturation)) / 100,
            max(0.01, min(1.0, brightness / 255)),
            dp["value_hexformat"],
        )
        dps = {dp["colour"]: colour}
        if not self._on or self._mode != "colour":
            dps[dp["switch"]] = True
            dps[dp["mode"]] = "colour"
        self._set(dps)
        self._on, self._mode = True, "colour"
        return True

    def stream_white(self, kelvin: int, brightness: int) -> None:
        """White light: kelvin 2000 (warm) – 6500 (cool), brightness 0–255."""
        dp = self._dev.dpset
        dps = {dp["switch"]: True, dp["mode"]: "white",
               dp["brightness"]: self._value(brightness / 255)}
        if dp["colourtemp"]:
            k = max(2000, min(6500, kelvin))
            dps[dp["colourtemp"]] = round((k - 2000) / (6500 - 2000) * dp["value_max"])
        self._set(dps)
        self._on, self._mode = True, "white"


def load_tuya_lights(path: str = DEVICES_FILE) -> list[TuyaLight]:
    """Connect to every colour bulb listed in tinytuya's devices.json.

    Returns [] when tinytuya isn't installed or the file doesn't exist, so
    CozyLife-only setups are unaffected.
    """
    if not TUYA_AVAILABLE or not os.path.exists(path):
        return []
    with open(path) as f:
        entries = json.load(f)

    lights = []
    for e in entries:
        label = e.get("name") or e.get("id")
        if not e.get("key"):
            continue
        if not e.get("ip"):
            print(f"  Tuya {label}: no IP in devices.json — rerun tuya_keys.py on this network")
            continue
        try:
            lights.append(TuyaLight(e["id"], e["ip"], e["key"],
                                    float(e.get("version") or 3.3), e.get("name", "")))
        except OSError as err:
            print(f"  Tuya {label}: skipped ({err})")
    return lights
