# This project was completely vibe coded.  

# No i do not know how to fix it 

# If you are human, and do know how to fix it, please feel free. Thanks. 

## QCII Two-Tone Detector (Raspberry Pi 5)

Headless service that listens for QCII-style two-tone paging pairs and energizes GPIO-driven relays based on configurable tone maps.

## Features
- Real-time detection using Goertzel filters, exact-frequency analysis, document-based FDMA/TDMA bucket decoding, and SNR thresholds.
- YAML configuration for tone pairs, GPIO actions, and audio settings.
- CLI utilities for live service, offline detection against WAV files, GPIO diagnostics/pulse testing, listing standard tone frequencies, and recording calibration samples.
- Systemd unit template for Raspberry Pi OS.

## Installation (Pi OS / Debian Trixie)

From the repo root, the shortest setup path is:

```bash
./scripts/install-debian.sh
qcii --config config/qcii.yaml
```

The installer does a few things for you:
- installs the OS/runtime pieces the program needs: `python3`, `python3-venv`, `python3-pip`, `python3-setuptools`, `python3-wheel`, `python3-cffi`, and `libportaudio2`
- installs distro-packaged Python dependencies when they are available: `click`, `numpy`, `pydantic`, `PyYAML`, `rich`, `scipy`, `sounddevice`, and `textual`
- creates `venv` with `--system-site-packages` so the virtualenv can reuse those apt-managed packages
- installs this project into the virtualenv, then creates `config/qcii.yaml` plus the `config/` and `logs/` directories if they do not already exist
- installs `/usr/local/bin/qcii` as a launcher that activates `venv` for you before starting the app

Anything the distro repo does not provide cleanly, notably `sounddevice`, is installed into the virtualenv by `pip` as part of the project install. On Raspberry Pi 5, `gpiozero` is also intentionally installed from `pip` so the app gets `gpiozero` 2.x instead of the older Debian package line, while the installer will also use `python3-lgpio` when the OS provides it because `gpiozero` 2.x prefers `LGPIOFactory` for modern boards.

For normal shell use, the repo includes `scripts/run-qcii.sh`, and the installer links it to `/usr/local/bin/qcii`. That means plain `qcii` still works even after you deactivate the virtualenv. For systemd boot startup, `scripts/run-qcii-service.sh` reuses that same launcher and starts the headless detector with `run --config ...`.

Direct Python packages imported by this project:
- `click`
- `gpiozero`
- `numpy`
- `pydantic`
- `PyYAML`
- `rich`
- `scipy`
- `sounddevice`
- `textual`

Optional test-only dependency:
- `pytest`

If you want the test dependency installed too:

```bash
./scripts/install-debian.sh --with-tests
venv/bin/python -m pytest
```

If you want to work inside the virtualenv manually, that still works too:

```bash
source venv/bin/activate
python -m pytest
```

Use Python 3.10 or newer. The project requires `>=3.10`.

## Configuration
Copy and edit `config.example.yaml`:
```bash
mkdir -p config logs
cp config.example.yaml config/qcii.yaml
```

Key fields:
- `audio.sample_rate`, `audio.frame_ms`: capture settings. Leave `audio.sample_rate` blank/null to use the selected input device's default rate; recording defaults to `44100`.
- `audio.device`: optional input device override. Leave it unset to auto-select a USB input, preferring names that look like Sabrent/USB audio adapters.
- `startup.auto_start_detection`: when enabled, the TUI will automatically start detection after a 5 second delay on launch.
- `tone_pairs`: list of tone pairs with durations, SNR threshold, and GPIO action (`gpio_pin`, `active_high`, `hold_ms`, `rearm_ms`, `repeat_suppression_ms`). `gpio_pin` uses BCM numbering, and the TUI `Test Pulse` button uses the currently selected tone pair's configured pin.
- `tone_pairs.dropout_tolerance_ms`: allows brief weak/noisy gaps inside tone A or B before the detector resets accumulation; useful for imperfect real-world QCII audio.
- Legacy `tolerance_pct` keys from older configs are ignored on load and dropped the next time the config is saved.
- Timing fields in milliseconds (`tone_a_ms`, `tone_b_ms`, `hold_ms`, `rearm_ms`, `repeat_suppression_ms`) must be between `100` and `10000`.
- `logging`: console or rotating file.

## Running
```bash
# Launches console TUI by default, even outside the virtualenv:
qcii --config config/qcii.yaml

# Run detector service headless using config/qcii.yaml by default:
qcii run

# Optional: override the config path explicitly
qcii run --config config/qcii.yaml
```

Offline detection:
```bash
qcii detect --config config/qcii.yaml --wav sample.wav
```

Repeatable decoder test using your saved tone-pair settings:
```bash
qcii generate-test-wav --config config/qcii.yaml --outfile /tmp/qcii-test.wav
qcii detect --config config/qcii.yaml --wav /tmp/qcii-test.wav
```

If you have more than one configured pair, choose one explicitly:
```bash
qcii generate-test-wav --config config/qcii.yaml --pair "Dispatch 1" --outfile /tmp/qcii-test.wav
```

You can also make the test harsher by adding silence, a gap, or deterministic noise:
```bash
qcii generate-test-wav --config config/qcii.yaml --outfile /tmp/qcii-test.wav --gap-ms 250 --noise-amplitude 0.02 --seed 42
```

Record calibration audio:
```bash
qcii record --seconds 5 --outfile sample.wav --device hw:1,0
```

List detected audio devices and see which one auto-selection would use:
```bash
qcii audio-devices
```

Show the active GPIO backend and pulse a relay/output pin directly through the same relay driver used by the TUI/service:
```bash
qcii gpio-status
qcii gpio-pulse --pin 17 --active-low --hold-ms 1000
```

Use your real BCM pin number there. If your relay board is active-high, switch `--active-low` to `--active-high`.

Console TUI (SSH-friendly) to edit config, start/stop live detection, pulse relays,
and view an in-app tail of the persistent log file plus a live input level meter:
```bash
qcii tui --config config/qcii.yaml
```

List standard QCII tones:
```bash
qcii list-tones --set fdma   # or --set tdma
```

## Systemd (optional)
The service template assumes the repo lives at `/opt/tonepi` and the virtualenv lives at `/opt/tonepi/venv`. It also forces `GPIOZERO_PIN_FACTORY=lgpio` so Pi 5 systems use the modern GPIO backend. The unit starts `scripts/run-qcii-service.sh`, which activates `venv` before launching headless `qcii run`. Edit `deploy/systemd/qcii.service` if you install it anywhere else, then place it in `/etc/systemd/system/`:
```bash
sudo cp deploy/systemd/qcii.service /etc/systemd/system/qcii.service
sudo systemctl daemon-reload
sudo systemctl enable qcii
sudo systemctl start qcii
```

If you prefer, the TUI can also enable or disable the same boot service for you with the `Enable On Boot` / `Disable On Boot` button.

## Testing
From the repo root:
```bash
./scripts/install-debian.sh --with-tests
source venv/bin/activate
python -m pytest
```

This project also defines a `test` extra in [pyproject.toml](/Users/adam/Documents/tonepi/pyproject.toml), so you can still use `venv/bin/python -m pip install --no-build-isolation '.[test]'` if you prefer the manual route.

## Hardware Notes
- Use a USB sound card with line-level input for radio interface.
- Drive relay modules through a transistor/MOSFET from the chosen GPIO pin; provide separate relay coil supply if needed.
- Set `action.active_high: false` for active-low relay hats that energize when the GPIO line is pulled low.
- Default logic leaves relay open on startup; re-arm windows prevent chatter.
