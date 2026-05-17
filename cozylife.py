"""
Standalone CozyLife device controller — no Home Assistant required.

Usage:
    # Auto-discover devices on your LAN
    devices = CozyLifeDevice.discover()
    for d in devices:
        print(d)

    # Connect directly by IP
    d = CozyLifeDevice("192.168.1.100")
    print(d.query())
    d.turn_on()
    d.set_brightness(128)
    d.set_color_temp(300)   # mireds (153–500)
    d.set_hs(240, 80)       # hue 0–360, saturation 0–100
    d.turn_off()
    d.close()
"""

import json
import socket
import time
from typing import Optional


_PORT = 5555
_DISCOVERY_PORT = 6095
_DISCOVERY_ADDR = "255.255.255.255"


def _sn() -> str:
    return str(int(time.time() * 1000))


class CozyLifeDevice:
    def __init__(self, ip: str):
        self.ip = ip
        self._sock: Optional[socket.socket] = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connect(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((self.ip, _PORT))
        self._sock = s

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __repr__(self):
        return f"CozyLifeDevice(ip={self.ip})"

    # ------------------------------------------------------------------
    # Low-level protocol
    # ------------------------------------------------------------------

    def _build(self, cmd: int, payload: dict) -> bytes:
        sn = _sn()
        if cmd == 3:    # SET
            msg = {"pv": 0, "cmd": cmd, "sn": sn,
                   "msg": {"attr": [int(k) for k in payload], "data": payload}}
        elif cmd == 2:  # QUERY
            msg = {"pv": 0, "cmd": cmd, "sn": sn, "msg": {"attr": [0]}}
        elif cmd == 0:  # INFO
            msg = {"pv": 0, "cmd": cmd, "sn": sn, "msg": {}}
        else:
            raise ValueError(f"Unknown cmd: {cmd}")
        return (json.dumps(msg, separators=(",", ":")) + "\r\n").encode()

    def _send(self, cmd: int, payload: dict = {}) -> None:
        self._sock.send(self._build(cmd, payload))

    def _send_recv(self, cmd: int, payload: dict = {}) -> dict:
        packet = self._build(cmd, payload)
        sn = json.loads(packet.strip())["sn"]
        self._sock.send(packet)

        for _ in range(10):
            try:
                raw = self._sock.recv(1024)
            except socket.timeout:
                break
            if sn.encode() in raw:
                resp = json.loads(raw.strip())
                return (resp.get("msg") or {}).get("data") or {}
        return {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def info(self) -> dict:
        """Return device identity (device ID, PID, MAC, firmware version, etc.)."""
        self._send(0)
        raw = self._sock.recv(1024)
        return json.loads(raw.strip()).get("msg", {})

    def query(self) -> dict:
        """
        Return current device state as a raw dpid→value dict, e.g.
        {'1': 255, '2': 0, '3': 500, '4': 600, '5': 0, '6': 0}
        Keys:
            '1' switch (0=off, 255=on)
            '2' work mode
            '3' color temp (0–1000, higher=warmer)
            '4' brightness (0–1000)
            '5' hue (0–360)
            '6' saturation (0–1000)
        """
        return self._send_recv(2)

    def turn_on(self) -> None:
        self._send(3, {"1": 255})

    def turn_off(self) -> None:
        self._send(3, {"1": 0})

    def set_brightness(self, brightness: int) -> None:
        """brightness: 0–255 (HA scale), converted internally to 0–1000."""
        value = max(0, min(255, brightness)) * 4
        self._send(3, {"1": 255, "4": value})

    def set_color_temp(self, mireds: int) -> None:
        """mireds: 153–500 (standard HA range)."""
        value = 1000 - max(153, min(500, mireds)) * 2
        self._send(3, {"1": 255, "3": value})

    def set_hs(self, hue: float, saturation: float) -> None:
        """hue: 0–360, saturation: 0–100."""
        self._send(3, {"1": 255, "5": int(hue), "6": int(saturation * 10)})

    def set_rgb(self, r: int, g: int, b: int) -> None:
        """Convenience: convert RGB (0–255 each) to hue/saturation and apply."""
        h, s = _rgb_to_hs(r, g, b)
        self.set_hs(h, s)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    @staticmethod
    def discover(timeout: float = 2.0) -> list["CozyLifeDevice"]:
        """
        Broadcast a UDP probe and return CozyLifeDevice instances for every
        device that responds.  Devices must be on the same subnet.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)

        probe = ('{"cmd":0,"pv":0,"sn":"' + _sn() + '","msg":{}}').encode()
        for _ in range(3):
            sock.sendto(probe, (_DISCOVERY_ADDR, _DISCOVERY_PORT))
            time.sleep(0.03)

        ips: list[str] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                _, addr = sock.recvfrom(1024)
                if addr[0] not in ips:
                    ips.append(addr[0])
            except socket.timeout:
                continue
        sock.close()

        devices = []
        for ip in ips:
            try:
                devices.append(CozyLifeDevice(ip))
            except OSError:
                pass
        return devices


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _rgb_to_hs(r: int, g: int, b: int) -> tuple[float, float]:
    r_, g_, b_ = r / 255, g / 255, b / 255
    cmax, cmin = max(r_, g_, b_), min(r_, g_, b_)
    delta = cmax - cmin

    if delta == 0:
        h = 0.0
    elif cmax == r_:
        h = 60 * (((g_ - b_) / delta) % 6)
    elif cmax == g_:
        h = 60 * ((b_ - r_) / delta + 2)
    else:
        h = 60 * ((r_ - g_) / delta + 4)

    s = 0.0 if cmax == 0 else (delta / cmax) * 100
    return h, s


# ------------------------------------------------------------------
# Quick CLI
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Discovering devices...")
        found = CozyLifeDevice.discover()
        if not found:
            print("No devices found.")
        for d in found:
            print(d, d.query())
        sys.exit(0)

    ip = sys.argv[1]
    cmd = sys.argv[2] if len(sys.argv) > 2 else "query"

    with CozyLifeDevice(ip) as d:
        if cmd == "on":
            d.turn_on()
        elif cmd == "off":
            d.turn_off()
        elif cmd == "info":
            print(d.info())
        elif cmd == "query":
            print(d.query())
        elif cmd == "brightness":
            d.set_brightness(int(sys.argv[3]))
        elif cmd == "temp":
            d.set_color_temp(int(sys.argv[3]))
        elif cmd == "hs":
            d.set_hs(float(sys.argv[3]), float(sys.argv[4]))
        elif cmd == "rgb":
            d.set_rgb(int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
        else:
            print(f"Unknown command: {cmd}")
