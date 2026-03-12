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
```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv python3-pip libopenblas-dev python3-scipy python3-numpy python3-yaml python3-sounddevice
# Optional: if you want to run the Textual TUI without pip-compiling it inside the venv:
# sudo apt-get install -y python3-rich python3-textual
python3.11 -m venv /opt/qcii-env
source /opt/qcii-env/bin/activate
pip install .
```

Use Python 3.10 or newer. The project requires `>=3.10`.

## Configuration
Copy and edit `config.example.yaml`:
```bash
cp config.example.yaml /etc/qcii.yaml
```

Key fields:
- `audio.sample_rate`, `audio.frame_ms`: capture settings (default 8000 Hz, 100 ms).
- `tone_pairs`: list of tone pairs with durations, tolerance, and GPIO action (`gpio_pin`, `hold_ms`, `rearm_ms`, `repeat_suppression_ms`).
- `logging`: console or rotating file.

## Running
```bash
# Launches console TUI by default:
qcii

# Run detector service explicitly:
qcii run --config /etc/qcii.yaml
```

Offline detection:
```bash
qcii detect --config /etc/qcii.yaml --wav sample.wav
```

Record calibration audio:
```bash
qcii record --seconds 5 --outfile sample.wav --device hw:1,0
```

Console TUI (SSH-friendly) to edit config and pulse relays:
```bash
qcii tui --config /etc/qcii.yaml
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
