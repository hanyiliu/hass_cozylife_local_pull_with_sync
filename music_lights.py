"""
music_lights.py — react CozyLife lights to speaker / system-audio output.

Unlike audio_lights.py (which uses the microphone), this script captures the
audio that is actually being *played* through your speakers, so the lights
react to whatever music or media is running on the computer.

Platform support
----------------
Linux   — PulseAudio / PipeWire: uses the default-sink monitor source
           (the virtual "loopback" input that mirrors speaker output).
macOS   — Requires a virtual-audio driver such as BlackHole or Soundflower.
           Install one and set it as the system output (or use a multi-output
           device so you still hear sound).  This script auto-detects common
           driver names.
Windows — Uses WASAPI loopback: no extra software needed; reads from the
           default render endpoint directly.

Requirements
------------
    pip install sounddevice numpy

Usage
-----
    python music_lights.py                           # auto-discover devices
    python music_lights.py 192.168.1.x 192.168.1.y  # specific device IPs
    python music_lights.py --list-audio              # list detected audio devices
    python music_lights.py --audio-device 3          # force a specific device ID
"""

import math
import platform
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from cozylife import CozyLifeDevice

# ------------------------------------------------------------------
# Tuning
# ------------------------------------------------------------------

SAMPLE_RATE    = 44100
CHUNK          = 2048          # larger block → better low-freq resolution
FREQ_MIN       = 60.0          # Hz — low end of mapped range (includes bass)
FREQ_MAX       = 8000.0        # Hz — high end of mapped range
SMOOTHING      = 0.20          # 0=instant, 1=never changes
GAIN_DECAY     = 0.995         # how fast auto-gain forgets loud peaks
MIN_GAIN       = 0.001         # prevents divide-by-zero in silence
LIGHT_INTERVAL = 0.05          # seconds between TCP sends per light

# macOS virtual-audio driver names to try (in priority order)
MACOS_LOOPBACK_NAMES = ["BlackHole", "Soundflower", "Loopback", "VB-Audio"]

# ------------------------------------------------------------------
# Audio device selection
# ------------------------------------------------------------------

def _find_loopback_device() -> tuple[int | None, int | None]:
    """Return (device_index, channel_count) for the best loopback source.

    Returns (None, None) if no suitable device is found.
    """
    system = platform.system()
    devices = sd.query_devices()

    if system == "Linux":
        # PulseAudio / PipeWire expose monitor sources as input devices.
        # Prefer the default-sink monitor; fall back to any monitor source.
        try:
            default_out = sd.query_devices(kind="output")
            default_name = default_out["name"]
            monitor_name = default_name + ".monitor"
            for i, d in enumerate(devices):
                if (d["name"] == monitor_name
                        and d["max_input_channels"] > 0):
                    return i, d["max_input_channels"]
        except Exception:
            pass
        # Generic fallback: first device with "monitor" in the name
        for i, d in enumerate(devices):
            if "monitor" in d["name"].lower() and d["max_input_channels"] > 0:
                return i, d["max_input_channels"]

    elif system == "Darwin":
        for name in MACOS_LOOPBACK_NAMES:
            for i, d in enumerate(devices):
                if (name.lower() in d["name"].lower()
                        and d["max_input_channels"] > 0):
                    return i, d["max_input_channels"]

    elif system == "Windows":
        # WASAPI loopback works on the *output* device index but opened as input
        try:
            default_out = sd.query_devices(kind="output")
            out_idx = devices.index(default_out) if isinstance(default_out, dict) else int(sd.default.device[1])
            return out_idx, default_out["max_output_channels"]
        except Exception:
            pass

    return None, None


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _open_loopback_stream(device_idx: int | None, channels: int,
                           callback) -> sd.InputStream:
    """Open a loopback/monitor InputStream with platform-appropriate settings."""
    kwargs: dict = dict(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK,
        channels=min(channels, 2),
        callback=callback,
    )
    if device_idx is not None:
        kwargs["device"] = device_idx

    if _is_windows():
        try:
            kwargs["extra_settings"] = sd.WasapiSettings(loopback=True)
        except AttributeError:
            pass  # older sounddevice — proceed without WASAPI loopback flag

    return sd.InputStream(**kwargs)


def list_audio_devices() -> None:
    """Print all audio input devices (for --list-audio)."""
    print("Available audio input / monitor devices:")
    print(f"{'ID':>4}  {'Channels':>8}  Name")
    print("-" * 60)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"{i:>4}  {d['max_input_channels']:>8}  {d['name']}")

    device_idx, _ = _find_loopback_device()
    print()
    if device_idx is not None:
        print(f"Auto-detected loopback device: [{device_idx}] "
              f"{sd.query_devices()[device_idx]['name']}")
    else:
        print("No loopback device auto-detected.")
        _print_platform_hint()


def _print_platform_hint() -> None:
    system = platform.system()
    if system == "Linux":
        print("Hint: ensure PulseAudio or PipeWire is running and a monitor"
              " source exists (pactl list short sources | grep monitor).")
    elif system == "Darwin":
        print("Hint: install BlackHole (https://github.com/ExistentialAudio/BlackHole)"
              " and route your system audio through it.")
    elif system == "Windows":
        print("Hint: WASAPI loopback should work automatically. Make sure"
              " sounddevice ≥ 0.4.0 is installed.")


# ------------------------------------------------------------------
# Shared reactive state
# ------------------------------------------------------------------

_lock      = threading.Lock()
_hue       = 0.0
_sat       = 90.0
_bri       = 0        # 0–255
_peak_rms  = MIN_GAIN
_running   = True
_lights_on = True


def _freq_to_hue(freq: float) -> float:
    """Log-map a frequency in [FREQ_MIN, FREQ_MAX] to hue [0, 360]."""
    freq   = max(FREQ_MIN, min(FREQ_MAX, freq))
    log_lo = math.log(FREQ_MIN)
    log_hi = math.log(FREQ_MAX)
    t      = (math.log(freq) - log_lo) / (log_hi - log_lo)
    return t * 360.0


def _audio_callback(indata, frames, time_info, status):
    global _hue, _sat, _bri, _peak_rms

    # Mix to mono (average channels)
    mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]

    rms = float(np.sqrt(np.mean(mono ** 2)))
    _peak_rms = max(rms, _peak_rms * GAIN_DECAY, MIN_GAIN)
    volume = min(rms / _peak_rms, 1.0)

    window   = np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(mono * window))
    freqs    = np.fft.rfftfreq(len(mono), 1.0 / SAMPLE_RATE)

    mask = (freqs >= FREQ_MIN) & (freqs <= FREQ_MAX)
    if mask.any():
        dominant_freq = float(freqs[mask][np.argmax(spectrum[mask])])
    else:
        dominant_freq = FREQ_MIN

    target_hue = _freq_to_hue(dominant_freq)
    target_bri = int(volume * 255)

    with _lock:
        _hue = _hue + SMOOTHING * (target_hue - _hue)
        _bri = int(_bri + SMOOTHING * (target_bri - _bri))
        _sat = 90.0


# ------------------------------------------------------------------
# Per-device light worker
# ------------------------------------------------------------------

def _light_worker(device: CozyLifeDevice, stop: threading.Event) -> None:
    while not stop.is_set():
        with _lock:
            on = _lights_on
            h  = _hue
            s  = _sat
            b  = _bri

        if on and b > 5:
            try:
                device._send(3, {
                    "1": 255,
                    "4": round(b * 1000 / 255),
                    "5": int(h),
                    "6": int(s * 10),
                })
            except OSError:
                # Socket closed by _send — wait, then reconnect if not stopping.
                if stop.wait(1.0):
                    return
                try:
                    device._reconnect()
                except OSError:
                    pass
                continue
        elif not on:
            try:
                device.turn_off()
            except OSError:
                pass

        stop.wait(LIGHT_INTERVAL)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    global _lights_on

    # Parse CLI args
    force_device: int | None = None
    ips: list[str] = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--list-audio":
            list_audio_devices()
            return
        elif args[i] == "--audio-device" and i + 1 < len(args):
            force_device = int(args[i + 1])
            i += 2
        else:
            ips.append(args[i])
            i += 1

    # Connect to lights
    if ips:
        print(f"Connecting to {ips}...")
        devices: list[CozyLifeDevice] = []
        for ip in ips:
            try:
                devices.append(CozyLifeDevice(ip))
            except OSError as e:
                print(f"  Could not connect to {ip}: {e}")
    else:
        print("Discovering devices...")
        devices = CozyLifeDevice.discover()

    if not devices:
        print("No devices found.")
        return

    print(f"Found {len(devices)} device(s): {[d.ip for d in devices]}")
    for d in devices:
        d.turn_on()

    # Select audio device
    if force_device is not None:
        dev_idx = force_device
        dev_info = sd.query_devices()[dev_idx]
        channels = dev_info["max_input_channels"] or 1
        print(f"Using audio device [{dev_idx}]: {dev_info['name']}")
    else:
        dev_idx, channels = _find_loopback_device()
        if dev_idx is None:
            print("\nCould not auto-detect a loopback/monitor audio device.")
            _print_platform_hint()
            print("Use --list-audio to see available devices, then retry with"
                  " --audio-device <ID>.")
            return
        print(f"Using loopback device [{dev_idx}]: "
              f"{sd.query_devices()[dev_idx]['name']}")

    # Start light workers
    stop = threading.Event()
    threads = [
        threading.Thread(target=_light_worker, args=(d, stop), daemon=True)
        for d in devices
    ]
    for t in threads:
        t.start()

    print("\nListening to speaker output...")
    print("Press Enter to toggle lights on/off, Ctrl+C to quit.\n")

    try:
        with _open_loopback_stream(dev_idx, channels or 2, _audio_callback):
            while True:
                input()
                _lights_on = not _lights_on
                print("Lights", "ON" if _lights_on else "OFF")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=7.0)
        for d in devices:
            try:
                d.turn_off()
                d.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
