from __future__ import annotations

import logging
import sys

import click
import numpy as np

from .config import load_config
from .detect import DetectorEngine, chunk_samples
from .logging_utils import configure_logging
from .service import run_service
from .tones import get_tone_set
from .tui import run_tui

LOG = logging.getLogger(__name__)


@click.group(invoke_without_command=True)
@click.option("--config", "default_config_path", default="/etc/qcii.yaml", show_default=True, type=click.Path())
@click.pass_context
def main(ctx: click.Context, default_config_path: str):
    """QCII two-tone detector utilities."""
    # Bare `qcii` launches the interactive TUI for convenience.
    if ctx.invoked_subcommand is None:
        run_tui(default_config_path)


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
@click.option(
    "--set",
    "tone_set",
    default="fdma",
    show_default=True,
    type=click.Choice(["fdma", "tdma"], case_sensitive=False),
    help="Tone set: fdma or tdma",
)
def list_tones(tone_set):
    """Print standard QCII tone frequencies for a tone set."""
    tones = get_tone_set(tone_set)
    print(f"Tone set: {tone_set}")
    for f in tones:
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
