from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import TonePair


def generate_tone_pair_samples(
    pair: TonePair,
    sample_rate: int,
    *,
    lead_in_ms: int = 250,
    gap_ms: int = 0,
    tail_ms: int = 250,
    amplitude: float = 0.8,
    noise_amplitude: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    def tone(freq_hz: float, duration_ms: int) -> np.ndarray:
        sample_count = int(sample_rate * duration_ms / 1000)
        t = np.arange(sample_count, dtype=np.float64) / sample_rate
        return amplitude * np.sin(2 * np.pi * freq_hz * t)

    def silence(duration_ms: int) -> np.ndarray:
        return np.zeros(int(sample_rate * duration_ms / 1000), dtype=np.float64)

    samples = np.concatenate(
        [
            silence(lead_in_ms),
            tone(pair.tone_a_hz, pair.tone_a_ms),
            silence(gap_ms),
            tone(pair.tone_b_hz, pair.tone_b_ms),
            silence(tail_ms),
        ]
    ).astype(np.float64)

    if noise_amplitude > 0:
        rng = np.random.default_rng(seed)
        samples += rng.normal(0.0, noise_amplitude, len(samples))

    peak = np.max(np.abs(samples))
    if peak > 1.0:
        samples = samples / peak
    return samples


def write_wav(path: str | Path, sample_rate: int, samples: np.ndarray) -> None:
    from scipy.io import wavfile

    clipped = np.clip(samples, -1.0, 1.0)
    pcm = np.round(clipped * 32767).astype(np.int16)
    wavfile.write(Path(path), sample_rate, pcm)
