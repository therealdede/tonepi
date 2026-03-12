from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import numpy as np

from .config import ServiceConfig, load_config
from .detect import DetectorEngine, chunk_samples
from .logging_utils import configure_logging
from .service import run_service
from .tones import QCII_FREQUENCIES_HZ
from .tui import run_tui

LOG = logging.getLogger(__name__)


@click.group()
def main():
    """QCII two-tone detector utilities."""


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def run(config_path):
    """Run the live detector service."""
    run_service(config_path)


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--wav", "wav_path", required=True, type=click.Path(exists=True))
def detect(config_path, wav_path):
    """Run offline detection against a WAV file."""
    cfg = load_config(config_path)
    configure_logging(cfg.logging)
    try:
        from scipy.io import wavfile
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"scipy needed for WAV operations: {exc}") from exc

    sr, data = wavfile.read(wav_path)
    if sr != cfg.audio.sample_rate:
        raise SystemExit(f"Sample rate mismatch: file {sr} != config {cfg.audio.sample_rate}")
    mono = data
    if mono.ndim > 1:
        mono = mono[:, 0]
    mono = mono.astype(np.float64) / (np.max(np.abs(mono)) + 1e-9)

    engine = DetectorEngine(cfg)
    for idx, chunk in enumerate(chunk_samples(mono, cfg.frame_samples)):
        ts = idx * cfg.audio.frame_ms
        events = engine.process_block(chunk, ts)
        for ev in events:
            print(f"{ts} ms -> {ev.pair.name}")


@main.command()
def list_tones():
    """Print standard QCII tone frequencies."""
    for f in QCII_FREQUENCIES_HZ:
        print(f"{f:7.1f} Hz")


@main.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
def tui(config_path):
    """Launch console (SSH-friendly) TUI to edit config and test relays."""
    run_tui(config_path)


@main.command()
@click.option("--seconds", default=5, show_default=True)
@click.option("--outfile", required=True, type=click.Path())
@click.option("--device", default=None, help="ALSA device id or name")
@click.option("--sample-rate", default=8000, show_default=True)
def record(seconds, outfile, device, sample_rate):
    """Record audio to WAV for calibration."""
    try:
        import sounddevice as sd
        from scipy.io import wavfile
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"sounddevice and scipy required: {exc}") from exc

    frames = int(seconds * sample_rate)
    click.echo(f"Recording {seconds}s from device {device or 'default'} ...")
    data = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32", device=device)
    sd.wait()
    wavfile.write(outfile, sample_rate, data)
    click.echo(f"Wrote {outfile}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
