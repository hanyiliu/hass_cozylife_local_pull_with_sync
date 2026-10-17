"""
mac_system_audio.py — listen to everything the Mac is playing, no BlackHole.

macOS 14.2+ can "tap" the audio apps send to the speakers.  SystemAudioTap
wraps a tap of all apps in a private aggregate device, which then shows up to
sounddevice like any other input, named NAME.  Sound keeps playing normally.

    pip install pyobjc-framework-CoreAudio

The first time a tap is read, macOS asks whether your terminal may record
system audio (System Settings → Privacy & Security → Screen & System Audio
Recording).  Until that's allowed the device delivers silence.

Check it from a terminal while music plays:

    python mac_system_audio.py
"""

import platform

NAME = "Mac System Audio"

try:
    import CoreAudio as _CA
    from Foundation import NSUUID as _NSUUID
    AVAILABLE = platform.system() == "Darwin" and hasattr(_CA, "AudioHardwareCreateProcessTap")
except ImportError:
    AVAILABLE = False


def _key(k) -> str:
    # pyobjc exposes the aggregate-device dictionary keys as bytes
    return k.decode() if isinstance(k, bytes) else k


class SystemAudioTap:
    """A stereo tap of every app's output, exposed as the input device NAME.

    Both the tap and the aggregate device are private to this process and
    disappear when it exits.
    """

    def __init__(self) -> None:
        self._tap_id = None
        self._agg_id = None

    @property
    def running(self) -> bool:
        return self._agg_id is not None

    def start(self) -> None:
        """Create the tap and its device.  Re-scan sounddevice afterwards
        (sd._terminate(); sd._initialize()) so it shows up there."""
        if self.running:
            return
        if not AVAILABLE:
            raise OSError("system audio capture needs macOS 14.2+ and "
                          "pip install pyobjc-framework-CoreAudio")

        desc = _CA.CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_([])
        desc.setName_(NAME)
        desc.setPrivate_(True)
        err, tap_id = _CA.AudioHardwareCreateProcessTap(desc, None)
        if err:
            raise OSError(f"couldn't create the system audio tap (OSStatus {err})")

        device = {
            _key(_CA.kAudioAggregateDeviceNameKey):         NAME,
            _key(_CA.kAudioAggregateDeviceUIDKey):          f"cozylife-tap-{_NSUUID.UUID().UUIDString()}",
            _key(_CA.kAudioAggregateDeviceIsPrivateKey):    True,
            _key(_CA.kAudioAggregateDeviceIsStackedKey):    False,
            _key(_CA.kAudioAggregateDeviceTapAutoStartKey): True,
            _key(_CA.kAudioAggregateDeviceTapListKey): [{
                _key(_CA.kAudioSubTapDriftCompensationKey): True,
                _key(_CA.kAudioSubTapUIDKey):               desc.UUID().UUIDString(),
            }],
        }
        err, agg_id = _CA.AudioHardwareCreateAggregateDevice(device, None)
        if err:
            _CA.AudioHardwareDestroyProcessTap(tap_id)
            raise OSError(f"couldn't create the system audio device (OSStatus {err})")
        self._tap_id, self._agg_id = tap_id, agg_id

    def stop(self) -> None:
        if self._agg_id is not None:
            _CA.AudioHardwareDestroyAggregateDevice(self._agg_id)
        if self._tap_id is not None:
            _CA.AudioHardwareDestroyProcessTap(self._tap_id)
        self._tap_id = self._agg_id = None


def _check(seconds: float = 3.0) -> None:
    """Record a few seconds of system audio and say whether anything was heard."""
    import numpy as np
    import sounddevice as sd

    tap = SystemAudioTap()
    tap.start()
    try:
        sd._terminate()
        sd._initialize()
        idx = next(i for i, d in enumerate(sd.query_devices()) if d["name"] == NAME)
        sr  = int(sd.query_devices(idx)["default_samplerate"])
        print(f"Listening to {NAME} for {seconds:.0f} s — play something...")
        x = sd.rec(int(seconds * sr), samplerate=sr, channels=2, device=idx, blocking=True)
        rms = float(np.sqrt(np.mean(x ** 2)))
        if rms > 1e-5:
            print(f"Heard it (level {rms:.4f}). MUSIC / RAVE can use the '{NAME}' Source.")
        else:
            print("Silence. Play something and run this again; if it's still silent, allow\n"
                  "your terminal app in System Settings → Privacy & Security →\n"
                  "Screen & System Audio Recording (System Audio Recording Only).")
    finally:
        tap.stop()


if __name__ == "__main__":
    _check()
