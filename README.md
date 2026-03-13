# THIS PROJECT IS WRITTEN COMPLETELY BY AI. YES IT IS PROBABLY GARBAGE. 

# NO I DO NOT KNOW HOW TO FIX IT. 

# IF YOU ARE HUMAN, AND DO KNOW HOW TO FIX IT, PLEASE FEEL FREE. THANK YOU.

## QCII Two-Tone Detector (Raspberry Pi 5)

Headless service that listens for Motorola Quick Call II (QCII) two-tone paging pairs and energizes GPIO-driven relays based on configurable tone maps.

## Features
- Real-time detection using Goertzel filters; configurable tolerance and SNR thresholds.
- YAML configuration for tone pairs, GPIO actions, and audio settings.
- CLI utilities for live service, offline detection against WAV files, listing standard tone frequencies, and recording calibration samples.
- Systemd unit template for Raspberry Pi OS.

## Installation (Pi OS / Debian Trixie)

I designed this to be a "lazy" install, meaning you should just be able to copy and paste each step. as with everything, ymmv. 

do a git clone, then cd to tonepi, then
- open our "sandbox" so we dont mess with system python

```
python3 -m venv venv
source venv/bin/activate
```
- *!!Temporarily Kinda Broken!!*    Make sure we have all of the packages that we need. 

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip libopenblas-dev python3-scipy python3-numpy python3-yaml python3-sounddevice libportaudio2 portaudio19-dev
```
- Do some stuff so that the program can talk outside of the venv to the hardware correctly
```
 deactivate 2>/dev/null
 rm -rf venv
python3 -m venv --system-site-packages venv
source venv/bin/activate
```
- dont know what this is for, skip it i guess???

```
# Optional: if you want to run the Textual TUI without pip-compiling it inside the venv:
# sudo apt-get install -y python3-rich python3-textual
```
- does some configuration for the audio libraries, then actually installs the thing
```
sudo ldconfig
pip install .
```
- Makes our config file and directory, and makes logs directory, only has to run onne on first start.
```
mkdir -p config logs
cp config.example.yaml config/qcii.yaml
qcii --config config/qcii.yaml
```
Use Python 3.10 or newer. The project requires `>=3.10`.

## Configuration
Copy and edit `config.example.yaml`:
```bash
mkdir -p config logs
cp config.example.yaml config/qcii.yaml
```

Key fields:
- `audio.sample_rate`, `audio.frame_ms`: capture settings (default 8000 Hz, 100 ms).
- `audio.device`: optional input device override. Leave it unset to auto-select a USB input, preferring names that look like Sabrent/USB audio adapters.
- `tone_pairs`: list of tone pairs with durations, tolerance, and GPIO action (`gpio_pin`, `hold_ms`, `rearm_ms`, `repeat_suppression_ms`).
- `logging`: console or rotating file.

## Running
```bash
# Launches console TUI by default:
qcii --config config/qcii.yaml

# Run detector service explicitly:
qcii run --config config/qcii.yaml
```

Offline detection:
```bash
qcii detect --config config/qcii.yaml --wav sample.wav
```

Record calibration audio:
```bash
qcii record --seconds 5 --outfile sample.wav --device hw:1,0
```

List detected audio devices and see which one auto-selection would use:
```bash
qcii audio-devices
```

Console TUI (SSH-friendly) to edit config, start/stop live detection, pulse relays,
and view an in-app tail of the persistent log file:
```bash
qcii tui --config config/qcii.yaml
```

List standard QCII tones:
```bash
qcii list-tones --set fdma   # or --set tdma
```

## Systemd (optional)
Edit `deploy/systemd/qcii.service` and place in `/etc/systemd/system/`:
```bash
sudo cp deploy/systemd/qcii.service /etc/systemd/system/qcii.service
sudo systemctl daemon-reload
sudo systemctl enable qcii
sudo systemctl start qcii
```

## Testing
From the repo root:
```bash
pip install '.[test]'
pytest
```

This project now defines a `test` extra in [pyproject.toml](/Users/adam/Documents/tonepi/pyproject.toml), so the command above installs `pytest` for the local environment.

## Hardware Notes
- Use a USB sound card with line-level input for radio interface.
- Drive relay modules through a transistor/MOSFET from the chosen GPIO pin; provide separate relay coil supply if needed.
- Default logic leaves relay open on startup; re-arm windows prevent chatter.
