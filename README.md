# CozyLife & Home Assistant + Disco + Audio Reactive Mode + No Home Assistant Dependency

CozyLife Assistant integration is developed for controlling CozyLife devices using local net, officially
maintained by the CozyLife Team.


## Supported Device Types

- RGBCW Light
- CW Light
- Switch & Plug
- Tuya / Smart Life colour bulbs (standalone scripts only — see below)


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

### Quick start

Needs Python 3.10+ on the same Wi-Fi network as the lights (with AP / client
isolation off on the router).

**1. Install**

```bash
git clone https://github.com/hanyiliu/hass_cozylife_local_pull_with_sync.git
cd hass_cozylife_local_pull_with_sync
python3 -m venv .venv
source .venv/bin/activate
pip install numpy sounddevice                         # audio + music modes
pip install tinytuya tuya-device-sharing-sdk segno    # Tuya / Smart Life bulbs
```

**2. Fetch your Tuya bulbs' keys** (once, and again if you add bulbs)

In Smart Life, open Me → ⚙ Settings → Account and Security → User Code, then:

```bash
python tuya_keys.py <user code>
```

Scan the QR code it shows with the Smart Life app and approve. This writes
`devices.json`. Skip this step if you only have CozyLife lights.

**3. Route your speaker audio for music mode** (macOS, once)

Music mode listens to what your Mac is playing through a loopback driver:

1. `brew install blackhole-2ch` (or the installer from [BlackHole](https://github.com/ExistentialAudio/BlackHole)), then restart.
2. In Audio MIDI Setup, click **+** → **Create Multi-Output Device**, and tick
   your speakers/headphones and **BlackHole 2ch**.
3. In System Settings → Sound → Output, choose the Multi-Output Device.

DJing in **Serato DJ Pro**? Its audio goes straight to your controller, so the
Mac's output (and BlackHole) never hears it. Instead, tick Setup → Audio →
**Make Audio Output Available to Other Applications** in Serato; music mode
picks up **Serato Virtual Audio** automatically. The MUSIC meter in `master.py`
shows which source it's listening to.

Linux uses the PulseAudio/PipeWire monitor source and Windows uses WASAPI
loopback automatically, so skip this step there.

**4. Run**

```bash
python master.py
```

Press **5** for MUSIC mode and play something. CozyLife lights are found
automatically and Tuya bulbs come from `devices.json`; they're all listed at
the bottom of the screen.

---

### `master.py` — control panel for all lights

A terminal UI that runs every mode on all CozyLife and Tuya lights at once,
with settings you can tweak live.

```bash
python master.py                              # auto-discover CozyLife + devices.json
python master.py 192.168.1.100 192.168.1.101 # specific CozyLife IPs (+ devices.json)
```

| Key | Action |
|---|---|
| `1`–`5` | OFF / DISCO / AUDIO (microphone) / MANUAL / MUSIC (speaker output) |
| `↑` / `↓` | Select a setting |
| `←` / `→` | Change it |
| `r` | Rediscover lights |
| `d` | Toggle the per-light debug panel (send rate, socket buffers) |
| `q` / `Esc` | Quit and turn everything off |

`Hue Spread` (AUDIO / MUSIC) offsets each light's colour so they don't all match.

---

### `music_lights.py` — speaker visualizer

The MUSIC mode on its own, without the UI: lights react to whatever the
computer is playing (see Quick start step 3 for macOS).

```bash
python music_lights.py                     # auto-discover
python music_lights.py --list-audio        # show audio inputs and the detected loopback
python music_lights.py --audio-device 3    # force a specific input
```

- **Enter** — toggle lights on/off
- **Ctrl+C** — quit and turn everything off

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

### Tuya / Smart Life lights

`master.py`, `music_lights.py` and `audio_lights.py` drive Tuya-based Wi-Fi
bulbs (the ones set up in the Smart Life or Tuya Smart app) alongside the
CozyLife lights, through `tuya_light.py`. `disco.py` is CozyLife-only.

Tuya bulbs encrypt their local protocol with a per-device key that only Tuya's
cloud hands out. `tuya_keys.py` (Quick start step 2) gets it with a QR login
through Smart Life — the approval screen says "Home Assistant" because it uses
the same login as Home Assistant's Tuya integration. If you'd rather use a Tuya
developer account: create a Cloud project at
[platform.tuya.com](https://platform.tuya.com), link your Smart Life account to
it, then run `python -m tinytuya wizard` here and answer "y" to poll devices.

Either way you get `devices.json` (device IDs, local keys and IPs) next to the
scripts. They load it on startup and skip anything that isn't a colour bulb.
It holds secrets and is gitignored. If a bulb's IP changes, rerun
`tuya_keys.py` or edit its `ip` in `devices.json`.

Tuya bulbs queue commands they can't apply in time, which shows up as growing
lag. Each bulb answers every command ~100 ms later, so `tuya_light.py` waits for
that answer before sending the next colour — every bulb runs as fast as it can
(about 8 updates/s for A19 bulbs) and never falls behind the music.

---

### Feedback

* Please submit an issue
* Send an email with the subject `hass support` to info@cozylife.app
