# CozyLife & Home Assistant + Disco + Audio Reactive Mode + No Home Assistant Dependency

CozyLife Assistant integration is developed for controlling CozyLife devices using local net, officially
maintained by the CozyLife Team.


## Supported Device Types

- RGBCW Light
- CW Light
- Switch & Plug


## Home Assistant Integration

### Install

* A Home Assistant environment that can access the local network
* Clone the repo to your `custom_components` directory
* Add to `configuration.yaml`:

```yaml
hass_cozylife_local_pull:
  lang: en
  ip:
    - "192.168.1.99"   # optional — add IPs manually if UDP discovery misses them
```

* Restart Home Assistant

### Troubleshoot

* Check whether internal network isolation (AP isolation) is enabled on your router — it will block discovery
* Make sure the `custom_components/hass_cozylife_local_pull/` folder is in the right place
* Restart HASS and check logs (`Settings → System → Logs`, filter for `hass_cozylife_local_pull`)


---


## Standalone Python Scripts

Control devices directly from any machine on the same network — no Home Assistant required.

### Requirements

```bash
pip install sounddevice numpy   # numpy only needed for audio_lights.py
```

---

### `cozylife.py` — Python library & CLI

A zero-dependency wrapper for discovering and controlling devices.

**As a library:**

```python
from cozylife import CozyLifeDevice

# Auto-discover all devices on the LAN
devices = CozyLifeDevice.discover()

# Or connect directly by IP
with CozyLifeDevice("192.168.1.100") as d:
    print(d.query())        # raw state dict
    d.turn_on()
    d.set_brightness(180)   # 0–255
    d.set_color_temp(300)   # mireds, 153–500
    d.set_hs(240, 80)       # hue 0–360, saturation 0–100
    d.set_rgb(255, 100, 0)  # orange
    d.turn_off()
```

**As a CLI:**

```bash
python cozylife.py                           # discover all devices
python cozylife.py 192.168.1.100 query
python cozylife.py 192.168.1.100 on
python cozylife.py 192.168.1.100 off
python cozylife.py 192.168.1.100 brightness 128
python cozylife.py 192.168.1.100 temp 300
python cozylife.py 192.168.1.100 hs 240 80
python cozylife.py 192.168.1.100 rgb 255 100 0
```

---

### `disco.py` — random color flasher

Floods all discovered lights with random colors simultaneously using one thread per light.

```bash
python disco.py                              # auto-discover
python disco.py 192.168.1.100 192.168.1.101 # specific IPs
```

- **Enter** — toggle lights on/off
- **Ctrl+C** — quit and turn everything off

Adjust `INTERVAL` at the top of the file to change speed (default `0.05s`).

---

### `audio_lights.py` — microphone visualizer

Reacts lights to live audio from your laptop's microphone.

- **Frequency → hue**: bass (80 Hz) = red, mids = green, treble (8 kHz) = blue
- **Volume → brightness**: auto-gains to your room's loudness level

```bash
pip install sounddevice
python audio_lights.py                              # auto-discover
python audio_lights.py 192.168.1.100 192.168.1.101 # specific IPs
```

- **Enter** — toggle lights on/off
- **Ctrl+C** — quit and turn everything off

Tuning knobs at the top of the file:

| Variable | Effect |
|---|---|
| `SMOOTHING` | Higher = slower, smoother color transitions |
| `LIGHT_INTERVAL` | Lower = faster TCP updates to lights |
| `GAIN_DECAY` | Lower = adapts to volume changes faster |
| `FREQ_MIN` / `FREQ_MAX` | Frequency range mapped to the full color wheel |

---

### Feedback

* Please submit an issue
* Send an email with the subject `hass support` to info@cozylife.app
