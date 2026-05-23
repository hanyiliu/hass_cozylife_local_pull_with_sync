"""
master.py — interactive TUI master control for CozyLife lights.

Keys:
  1 / 2 / 3 / 4 / 5   switch mode: OFF / DISCO / AUDIO / MANUAL / MUSIC
  ↑ / ↓                navigate settings
  ← / →                decrease / increase selected setting value
  r                    rediscover devices
  q                    quit

Modes
-----
  AUDIO  — reacts to the microphone (ambient sound in the room)
  MUSIC  — reacts to the speaker output (music/media playing on this PC)

Usage:
  python master.py                          # auto-discover
  python master.py 192.168.1.x 192.168.1.y # specific IPs
"""

import curses
import math
import platform
import random
import sys
import threading
import time
from typing import Optional

from cozylife import CozyLifeDevice

try:
    import numpy as np
    import sounddevice as sd
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

# macOS virtual-audio driver names recognised as loopback sources
_MACOS_LOOPBACK_NAMES = ["BlackHole", "Soundflower", "Loopback", "VB-Audio"]


def _find_loopback_device() -> tuple[int | None, int | None]:
    """Return (device_index, channel_count) for the system-audio loopback source."""
    if not AUDIO_AVAILABLE:
        return None, None
    system = platform.system()
    devices = sd.query_devices()

    if system == "Linux":
        try:
            default_out = sd.query_devices(kind="output")
            monitor_name = default_out["name"] + ".monitor"
            for i, d in enumerate(devices):
                if d["name"] == monitor_name and d["max_input_channels"] > 0:
                    return i, d["max_input_channels"]
        except Exception:
            pass
        for i, d in enumerate(devices):
            if "monitor" in d["name"].lower() and d["max_input_channels"] > 0:
                return i, d["max_input_channels"]

    elif system == "Darwin":
        for name in _MACOS_LOOPBACK_NAMES:
            for i, d in enumerate(devices):
                if name.lower() in d["name"].lower() and d["max_input_channels"] > 0:
                    return i, d["max_input_channels"]

    elif system == "Windows":
        try:
            default_out = sd.query_devices(kind="output")
            out_idx = int(sd.default.device[1])
            return out_idx, default_out["max_output_channels"]
        except Exception:
            pass

    return None, None


def _open_loopback_stream(device_idx: int | None, channels: int, callback) -> "sd.InputStream":
    kwargs: dict = dict(
        samplerate=44100,
        blocksize=2048,
        channels=min(channels, 2),
        callback=callback,
    )
    if device_idx is not None:
        kwargs["device"] = device_idx
    if platform.system() == "Windows":
        try:
            kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
        except AttributeError:
            pass
    return sd.InputStream(**kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# Settings metadata — defines every tunable knob per mode
# ──────────────────────────────────────────────────────────────────────────────

SETTINGS_META: dict[str, list[dict]] = {
    "off": [],
    "disco": [
        {"key": "interval",  "label": "Interval",  "min": 0.01, "max": 2.0,    "step": 0.01, "fmt": "{:.2f}s"},
        {"key": "sat_min",   "label": "Sat Min",   "min": 0.0,  "max": 100.0,  "step": 5.0,  "fmt": "{:.0f}%"},
        {"key": "sat_max",   "label": "Sat Max",   "min": 0.0,  "max": 100.0,  "step": 5.0,  "fmt": "{:.0f}%"},
        {"key": "bri",       "label": "Brightness","min": 0.0,  "max": 255.0,  "step": 10.0, "fmt": "{:.0f}"},
    ],
    "audio": [
        {"key": "smoothing",      "label": "Smoothing",   "min": 0.01, "max": 0.99,   "step": 0.05,  "fmt": "{:.2f}"},
        {"key": "gain_decay",     "label": "Gain Decay",  "min": 0.90, "max": 0.999,  "step": 0.001, "fmt": "{:.3f}"},
        {"key": "freq_min",       "label": "Freq Min",    "min": 20.0, "max": 500.0,  "step": 10.0,  "fmt": "{:.0f} Hz"},
        {"key": "freq_max",       "label": "Freq Max",    "min": 1000.,"max": 20000., "step": 500.0, "fmt": "{:.0f} Hz"},
        {"key": "light_interval", "label": "Update Rate", "min": 0.02, "max": 0.5,    "step": 0.01,  "fmt": "{:.2f}s"},
        {"key": "hue_spread",     "label": "Hue Spread",  "min": 0.0,  "max": 180.0,  "step": 5.0,   "fmt": "{:.0f}°"},
    ],
    # Music mode shares the same DSP knobs as audio mode but reads from the
    # speaker loopback instead of the microphone.
    "music": [
        {"key": "smoothing",      "label": "Smoothing",   "min": 0.01, "max": 0.99,   "step": 0.05,  "fmt": "{:.2f}"},
        {"key": "gain_decay",     "label": "Gain Decay",  "min": 0.90, "max": 0.999,  "step": 0.001, "fmt": "{:.3f}"},
        {"key": "freq_min",       "label": "Freq Min",    "min": 20.0, "max": 500.0,  "step": 10.0,  "fmt": "{:.0f} Hz"},
        {"key": "freq_max",       "label": "Freq Max",    "min": 1000.,"max": 20000., "step": 500.0, "fmt": "{:.0f} Hz"},
        {"key": "light_interval", "label": "Update Rate", "min": 0.02, "max": 0.5,    "step": 0.01,  "fmt": "{:.2f}s"},
        {"key": "hue_spread",     "label": "Hue Spread",  "min": 0.0,  "max": 180.0,  "step": 5.0,   "fmt": "{:.0f}°"},
    ],
    "manual": [
        {"key": "hue",        "label": "Hue",        "min": 0.0,   "max": 360.0, "step": 2.0,  "fmt": "{:.0f}°"},
        {"key": "saturation", "label": "Saturation", "min": 0.0,   "max": 100.0, "step": 2.0,  "fmt": "{:.0f}%"},
        {"key": "brightness", "label": "Brightness", "min": 0.0,   "max": 255.0, "step": 5.0,  "fmt": "{:.0f}"},
        {"key": "color_temp", "label": "Color Temp", "min": 2000.0, "max": 6500.0, "step": 100.0, "fmt": "{:.0f} K"},
        {"key": "use_temp",   "label": "Color Mode", "min": 0.0,   "max": 1.0,   "step": 1.0,
         "fmt_fn": lambda v: "Hue / Sat" if v < 0.5 else "Color Temp"},
    ],
}

DEFAULT_SETTINGS: dict[str, dict] = {
    "off":    {},
    "disco":  {"interval": 0.05, "sat_min": 60.0, "sat_max": 100.0, "bri": 255.0},
    "audio":  {"smoothing": 0.25, "gain_decay": 0.995,
               "freq_min": 80.0, "freq_max": 8000.0, "light_interval": 0.05, "hue_spread": 0.0},
    "music":  {"smoothing": 0.20, "gain_decay": 0.995,
               "freq_min": 60.0, "freq_max": 8000.0, "light_interval": 0.05, "hue_spread": 0.0},
    "manual": {"hue": 0.0, "saturation": 90.0, "brightness": 200.0,
               "color_temp": 3500.0, "use_temp": 0.0},
}

BAR_W = 14


# ──────────────────────────────────────────────────────────────────────────────
# Controller
# ──────────────────────────────────────────────────────────────────────────────

class Controller:
    def __init__(self, devices: list[CozyLifeDevice]):
        self.devices  = devices
        self.mode     = "off"
        self.settings = {m: dict(v) for m, v in DEFAULT_SETTINGS.items()}
        self.sel      = 0
        self.status   = f"{len(devices)} device(s) connected"
        self.lock     = threading.Lock()

        # reactive state shared between audio/music callback and light workers
        self._a_hue   = 0.0
        self._a_bri   = 0
        self._a_sat   = 90.0
        self._a_peak  = 0.01
        self._a_stream: Optional[object] = None
        # music-mode loopback device (detected once, reused on mode switches)
        self._music_dev_idx: Optional[int] = None
        self._music_channels: int = 1

        self._stop    = threading.Event()
        self._workers: list[threading.Thread] = []

    # ── mode switching ────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        if mode in ("audio", "music") and not AUDIO_AVAILABLE:
            self.status = "audio unavailable — pip install sounddevice numpy"
            return

        if mode == "music":
            # Detect the loopback device once; cache for future switches.
            if self._music_dev_idx is None:
                dev_idx, channels = _find_loopback_device()
                if dev_idx is None:
                    self.status = ("music: no loopback device found — "
                                   "see music_lights.py --list-audio")
                    return
                self._music_dev_idx = dev_idx
                self._music_channels = channels or 2

        self._stop_workers()
        self.mode = mode
        self.sel  = 0

        if mode == "off":
            for d in self.devices:
                try:
                    d.turn_off()
                except OSError:
                    pass

        elif mode == "manual":
            for d in self.devices:
                try:
                    d.turn_on()
                except OSError:
                    pass
            self._push_manual()

        else:  # disco / audio / music
            self._stop.clear()
            for idx, d in enumerate(self.devices):
                t = threading.Thread(target=self._worker, args=(d, idx), daemon=True)
                t.start()
                self._workers.append(t)
            if mode == "audio":
                self._start_audio()
            elif mode == "music":
                self._start_music()

        self.status = f"Mode: {mode.upper()}"

    def _stop_workers(self) -> None:
        if self._a_stream:
            try:
                self._a_stream.stop()
                self._a_stream.close()
            except Exception:
                pass
            self._a_stream = None
        self._stop.set()
        for t in self._workers:
            # Allow up to 7 s: socket timeout (5 s) + reconnect overhead (2 s).
            t.join(timeout=7.0)
        self._workers.clear()

    # ── per-device workers ────────────────────────────────────────────────────

    def _worker(self, device: CozyLifeDevice, idx: int = 0) -> None:
        while not self._stop.is_set():
            mode = self.mode
            s    = self.settings.get(mode, {})
            try:
                if mode == "disco":
                    hue = random.uniform(0, 360)
                    sat = random.uniform(s.get("sat_min", 60), s.get("sat_max", 100))
                    bri = round(int(s.get("bri", 255)) * 1000 / 255)
                    device._send(3, {"1": 255, "4": bri,
                                     "5": int(hue), "6": int(sat * 10)})
                    self._stop.wait(s.get("interval", 0.05))

                elif mode in ("audio", "music"):
                    with self.lock:
                        h, b, sat = self._a_hue, self._a_bri, self._a_sat
                    h = (h + idx * s.get("hue_spread", 0.0)) % 360
                    if b > 5:
                        device._send(3, {"1": 255, "4": round(b * 1000 / 255),
                                         "5": int(h), "6": int(sat * 10)})
                    self._stop.wait(s.get("light_interval", 0.05))

            except OSError:
                device.close()
                # Wait briefly (honouring stop), then attempt a reconnect so
                # the worker recovers automatically if the device comes back.
                if self._stop.wait(1.0):
                    return
                try:
                    device._reconnect()
                except OSError:
                    pass  # next iteration will retry

    # ── audio ─────────────────────────────────────────────────────────────────

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0]
        s    = self.settings["audio"]
        rms  = float(np.sqrt(np.mean(mono ** 2)))
        self._a_peak = max(rms, self._a_peak * s["gain_decay"], 0.01)
        vol  = min(rms / self._a_peak, 1.0)

        win   = np.hanning(len(mono))
        spec  = np.abs(np.fft.rfft(mono * win))
        freqs = np.fft.rfftfreq(len(mono), 1.0 / 44100)
        fmin, fmax = s["freq_min"], s["freq_max"]
        mask  = (freqs >= fmin) & (freqs <= fmax)
        dom   = float(freqs[mask][np.argmax(spec[mask])]) if mask.any() else fmin
        t     = (math.log(max(dom, fmin)) - math.log(fmin)) / (math.log(fmax) - math.log(fmin))
        sm    = s["smoothing"]

        with self.lock:
            self._a_hue = self._a_hue + sm * (t * 360.0 - self._a_hue)
            self._a_bri = int(self._a_bri + sm * (vol * 255 - self._a_bri))

    def _start_audio(self) -> None:
        self._a_peak = 0.01
        stream = sd.InputStream(samplerate=44100, blocksize=1024,
                                channels=1, callback=self._audio_callback)
        stream.start()
        self._a_stream = stream

    def _music_callback(self, indata, frames, time_info, status) -> None:
        """Same DSP as _audio_callback but called from the loopback stream."""
        mono = indata.mean(axis=1) if indata.ndim > 1 and indata.shape[1] > 1 else indata[:, 0]
        s    = self.settings["music"]
        rms  = float(np.sqrt(np.mean(mono ** 2)))
        self._a_peak = max(rms, self._a_peak * s["gain_decay"], 0.001)
        vol  = min(rms / self._a_peak, 1.0)

        win   = np.hanning(len(mono))
        spec  = np.abs(np.fft.rfft(mono * win))
        freqs = np.fft.rfftfreq(len(mono), 1.0 / 44100)
        fmin, fmax = s["freq_min"], s["freq_max"]
        mask  = (freqs >= fmin) & (freqs <= fmax)
        dom   = float(freqs[mask][np.argmax(spec[mask])]) if mask.any() else fmin
        t     = (math.log(max(dom, fmin)) - math.log(fmin)) / (math.log(fmax) - math.log(fmin))
        sm    = s["smoothing"]

        with self.lock:
            self._a_hue = self._a_hue + sm * (t * 360.0 - self._a_hue)
            self._a_bri = int(self._a_bri + sm * (vol * 255 - self._a_bri))

    def _start_music(self) -> None:
        self._a_peak = 0.001
        stream = _open_loopback_stream(
            self._music_dev_idx, self._music_channels, self._music_callback
        )
        stream.start()
        self._a_stream = stream

    # ── settings ──────────────────────────────────────────────────────────────

    def nav(self, direction: int) -> None:
        meta = SETTINGS_META.get(self.mode, [])
        if meta:
            self.sel = (self.sel + direction) % len(meta)

    def adjust(self, direction: int) -> None:
        meta = SETTINGS_META.get(self.mode, [])
        if not meta or self.sel >= len(meta):
            return
        m   = meta[self.sel]
        key = m["key"]
        cur = self.settings[self.mode].get(key, m["min"])
        new = max(m["min"], min(m["max"], cur + direction * m["step"]))
        dec = len(str(m["step"]).split(".")[-1]) if "." in str(m["step"]) else 0
        self.settings[self.mode][key] = round(new, dec)
        if self.mode == "manual":
            self._push_manual()

    def _push_manual(self) -> None:
        s   = self.settings["manual"]
        bri = round(int(s.get("brightness", 200)) * 1000 / 255)
        if s.get("use_temp", 0) >= 0.5:
            k = s.get("color_temp", 3500)
            payload: dict = {"1": 255, "4": bri,
                             "3": round((k - 2000) / (6500 - 2000) * 1000)}
        else:
            payload = {"1": 255, "4": bri,
                       "5": int(s.get("hue", 0)),
                       "6": int(s.get("saturation", 90) * 10)}
        for d in self.devices:
            try:
                d._send(3, payload)
            except OSError:
                pass

    def shutdown(self) -> None:
        self._stop_workers()
        for d in self.devices:
            try:
                d.turn_off()
                d.close()
            except OSError:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# TUI drawing
# ──────────────────────────────────────────────────────────────────────────────

def _bar(val: float, lo: float, hi: float, width: int = BAR_W) -> str:
    frac   = (val - lo) / (hi - lo) if hi != lo else 0.0
    filled = min(int(frac * width), width)
    return "█" * filled + "░" * (width - filled)


def draw(stdscr, ctrl: Controller) -> None:
    stdscr.erase()
    rows, cols = stdscr.getmaxyx()

    # colour pairs
    C_TITLE = curses.color_pair(1) | curses.A_BOLD
    C_ACTIVE = curses.color_pair(2) | curses.A_BOLD
    C_IDLE   = curses.color_pair(3)
    C_SEL    = curses.color_pair(4)
    C_VAL    = curses.color_pair(5)
    C_DEV    = curses.color_pair(6)
    C_DIM    = curses.color_pair(7) | curses.A_DIM

    def put(row: int, col: int, text: str, attr: int = curses.A_NORMAL) -> int:
        if row >= rows - 1 or col >= cols - 1:
            return col
        avail = cols - col - 1
        try:
            stdscr.addstr(row, col, text[:avail], attr)
        except curses.error:
            pass
        return col + len(text)

    r = 0

    # title bar
    title = "  CozyLife Master Control  "
    put(r, max(0, (cols - len(title)) // 2), title, C_TITLE)
    r += 2

    # mode strip
    mode_defs = [
        ("off",    "1", " OFF    "),
        ("disco",  "2", " DISCO  "),
        ("audio",  "3", " AUDIO  "),
        ("manual", "4", " MANUAL "),
        ("music",  "5", " MUSIC  "),
    ]
    x = 2
    for mkey, num, label in mode_defs:
        unavail = mkey in ("audio", "music") and not AUDIO_AVAILABLE
        if unavail:
            attr = C_DIM
        elif mkey == ctrl.mode:
            attr = C_ACTIVE
        else:
            attr = C_IDLE
        x = put(r, x, f"[{num}]{label}", attr)
        x += 2
    r += 2

    # separator
    put(r, 0, "─" * (cols - 1), C_DIM)
    r += 1

    # settings panel
    meta = SETTINGS_META.get(ctrl.mode, [])
    if meta:
        put(r, 2, f"Settings — {ctrl.mode.upper()}", curses.A_BOLD)
        r += 1
        s = ctrl.settings.get(ctrl.mode, {})
        for i, m in enumerate(meta):
            val     = s.get(m["key"], m["min"])
            val_str = m["fmt_fn"](val) if "fmt_fn" in m else m["fmt"].format(val)
            bar_str = _bar(val, m["min"], m["max"]) if "fmt_fn" not in m else None
            is_sel  = i == ctrl.sel
            prefix  = "  ▶ " if is_sel else "    "
            label_a = C_SEL if is_sel else curses.A_NORMAL
            val_a   = C_VAL | (curses.A_BOLD if is_sel else 0)

            cx = put(r, 0, f"{prefix}{m['label']:<14}", label_a)
            if bar_str:
                cx = put(r, cx, f" [{bar_str}]", C_DIM)
            cx = put(r, cx, f"  {val_str}", val_a)
            if is_sel:
                put(r, cx, "   ← →", C_DIM)
            r += 1
    else:
        put(r, 2, "All lights off." if ctrl.mode == "off" else "No settings.", C_DIM)
        r += 1

    # live reactive meters (audio and music modes)
    if ctrl.mode in ("audio", "music") and AUDIO_AVAILABLE:
        r += 1
        src_label = "microphone" if ctrl.mode == "audio" else "speaker output"
        put(r, 2, f"Live ({src_label}):", curses.A_BOLD)
        r += 1
        with ctrl.lock:
            ah, ab = ctrl._a_hue, ctrl._a_bri
        cx = put(r, 4, "Hue ", C_DIM)
        cx = put(r, cx, f"[{_bar(ah, 0, 360, 20)}]", C_VAL)
        put(r, cx, f"  {ah:5.0f}°", C_DIM)
        r += 1
        cx = put(r, 4, "Bri ", C_DIM)
        cx = put(r, cx, f"[{_bar(ab, 0, 255, 20)}]", C_VAL)
        put(r, cx, f"  {ab:3d}", C_DIM)
        r += 1

    r += 1
    put(r, 0, "─" * (cols - 1), C_DIM)
    r += 1

    # device list
    put(r, 2, f"Devices ({len(ctrl.devices)} connected)", curses.A_BOLD)
    r += 1
    for d in ctrl.devices:
        put(r, 4, f"● {d.ip}", C_DEV)
        r += 1

    # status
    if ctrl.status:
        r += 1
        put(r, 2, ctrl.status, C_DIM)

    # help strip pinned to bottom
    help_row = rows - 2
    if help_row > r:
        put(help_row, 0, "─" * (cols - 1), C_DIM)
        put(help_row + 1, 0,
            "  ↑/↓ navigate   ←/→ adjust   1-5 switch mode   r rediscover   q/Esc quit",
            C_DIM)

    stdscr.refresh()


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────

def _run(stdscr, devices: list[CozyLifeDevice]) -> None:
    curses.curs_set(0)
    stdscr.nodelay(True)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)   # title
    curses.init_pair(2, curses.COLOR_GREEN, -1)                   # active mode
    curses.init_pair(3, curses.COLOR_WHITE, -1)                   # idle mode
    curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_WHITE)  # selected row
    curses.init_pair(5, curses.COLOR_CYAN,  -1)                   # value
    curses.init_pair(6, curses.COLOR_GREEN, -1)                   # device bullet
    curses.init_pair(7, curses.COLOR_WHITE, -1)                   # dim text

    ctrl = Controller(devices)

    try:
        while True:
            draw(stdscr, ctrl)
            key = stdscr.getch()

            if key in (ord("q"), 27):   # q or Escape
                break
            elif key == ord("1"):
                ctrl.set_mode("off")
            elif key == ord("2"):
                ctrl.set_mode("disco")
            elif key == ord("3"):
                ctrl.set_mode("audio")
            elif key == ord("4"):
                ctrl.set_mode("manual")
            elif key == ord("5"):
                ctrl.set_mode("music")
            elif key == curses.KEY_UP:
                ctrl.nav(-1)
            elif key == curses.KEY_DOWN:
                ctrl.nav(1)
            elif key == curses.KEY_LEFT:
                ctrl.adjust(-1)
            elif key == curses.KEY_RIGHT:
                ctrl.adjust(1)
            elif key == ord("r"):
                ctrl.status = "Rediscovering..."
                draw(stdscr, ctrl)
                ctrl.shutdown()
                new_devices = CozyLifeDevice.discover()
                ctrl = Controller(new_devices)
                ctrl.status = f"Found {len(new_devices)} device(s)"
            elif key == curses.KEY_RESIZE:
                pass  # redraws automatically next tick

            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        ctrl.shutdown()


def main() -> None:
    if len(sys.argv) > 1:
        ips = sys.argv[1:]
        print(f"Connecting to {ips}...")
        devices = []
        for ip in ips:
            try:
                devices.append(CozyLifeDevice(ip))
            except OSError as e:
                print(f"  {ip}: {e}")
    else:
        print("Discovering devices...")
        devices = CozyLifeDevice.discover()

    if not devices:
        print("No devices found. Try: python master.py 192.168.1.x")
        return

    print(f"Connected to {len(devices)} device(s). Launching TUI...")
    time.sleep(0.3)
    curses.wrapper(_run, devices)


if __name__ == "__main__":
    main()
