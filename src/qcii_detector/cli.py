from __future__ import annotations

import logging
import sys

import click
import numpy as np

from .audio_devices import auto_select_input_device, list_audio_devices, resolve_sample_rate
from .config import DEFAULT_CONFIG_PATH, load_config
from .detect import DetectorEngine, chunk_samples
from .logging_utils import configure_logging
from .service import run_service
from .synth import generate_tone_pair_samples, write_wav
from .tones import get_tone_set
from .tui import run_tui

LOG = logging.getLogger(__name__)


@click.group(invoke_without_command=True)
@click.option(
    "--config",
    "default_config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    type=click.Path(),
)
@click.pass_context
def main(ctx: click.Context, default_config_path: str):
    """QCII two-tone detector utilities."""
    # Bare `qcii` launches the interactive TUI for convenience.
    if ctx.invoked_subcommand is None:
        run_tui(default_config_path)


@main.command()
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    type=click.Path(exists=True),
)
def run(config_path):
    """Run the live detector service."""
    run_service(config_path)


@main.command()
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    type=click.Path(exists=True),
)
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
    effective_sample_rate = resolve_sample_rate(cfg.audio.device, cfg.audio.sample_rate)
    cfg.audio.sample_rate = sr if cfg.audio.sample_rate is None else effective_sample_rate
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


@main.command("generate-test-wav")
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    type=click.Path(exists=True),
)
@click.option("--outfile", required=True, type=click.Path())
@click.option(
    "--pair",
    "pair_name",
    default=None,
    help="Tone pair name to synthesize. Defaults to the only configured pair.",
)
@click.option("--lead-in-ms", default=0, show_default=True, type=int)
@click.option("--gap-ms", default=0, show_default=True, type=int)
@click.option("--tail-ms", default=0, show_default=True, type=int)
@click.option("--amplitude", default=0.8, show_default=True, type=float)
@click.option("--noise-amplitude", default=0.0, show_default=True, type=float)
@click.option("--seed", default=0, show_default=True, type=int)
def generate_test_wav(
    config_path,
    outfile,
    pair_name,
    lead_in_ms,
    gap_ms,
    tail_ms,
    amplitude,
    noise_amplitude,
    seed,
):
    """Generate a deterministic QCII WAV fixture from the current config."""
    cfg = load_config(config_path)
    effective_sample_rate = resolve_sample_rate(cfg.audio.device, cfg.audio.sample_rate)
    cfg.audio.sample_rate = effective_sample_rate

    pair = _resolve_tone_pair(cfg, pair_name)
    samples = generate_tone_pair_samples(
        pair,
        cfg.audio.sample_rate,
        lead_in_ms=lead_in_ms,
        gap_ms=gap_ms,
        tail_ms=tail_ms,
        amplitude=amplitude,
        noise_amplitude=noise_amplitude,
        seed=seed,
    )
    write_wav(outfile, cfg.audio.sample_rate, samples)
    click.echo(
        f"Wrote {outfile} for pair '{pair.name}' "
        f"({pair.tone_a_hz:.1f} Hz/{pair.tone_b_hz:.1f} Hz at {cfg.audio.sample_rate} Hz)"
    )


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
        formatted = f"{f:.2f}".rstrip("0").rstrip(".")
        print(f"{formatted:>8} Hz")


@main.command()
@click.option(
    "--config",
    "config_path",
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    type=click.Path(exists=True),
)
def tui(config_path):
    """Launch console (SSH-friendly) TUI to edit config and test relays."""
    run_tui(config_path)


@main.command()
@click.option("--seconds", default=5, show_default=True)
@click.option("--outfile", required=True, type=click.Path())
@click.option("--device", default=None, help="ALSA device id or name")
@click.option("--sample-rate", default=44100, show_default=True)
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


@main.command("audio-devices")
def audio_devices():
    """List available audio devices and highlight the auto-selected input."""
    try:
        devices = list_audio_devices()
        selected = auto_select_input_device()
    except Exception as exc:
        raise SystemExit(f"Unable to enumerate audio devices: {exc}") from exc

    if not devices:
        click.echo("No audio devices found.")
        return

    for device in devices:
        markers = []
        if device.is_input:
            markers.append("input")
        if selected and device.index == selected.index:
            markers.append("auto")
        marker_text = f" [{' '.join(markers)}]" if markers else ""
        click.echo(
            f"{device.index:>2}: {device.name}{marker_text} "
            f"(in={device.max_input_channels}, out={device.max_output_channels}, "
            f"default_sr={device.default_samplerate:.0f})"
        )


def _resolve_tone_pair(cfg, pair_name):
    if pair_name is None:
        if len(cfg.tone_pairs) == 1:
            return cfg.tone_pairs[0]
        raise SystemExit("Multiple tone pairs configured; pass --pair with an exact pair name.")

    for pair in cfg.tone_pairs:
        if pair.name == pair_name:
            return pair
    raise SystemExit(f"Unknown tone pair '{pair_name}'.")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
