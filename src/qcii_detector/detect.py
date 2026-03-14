from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

import numpy as np

from .config import ServiceConfig, TonePair


def db10(value: float) -> float:
    return 10 * math.log10(max(value, 1e-12))


class GoertzelBank:
    """Compute Goertzel power for a set of frequencies over fixed-size blocks."""

    def __init__(self, freqs: Sequence[float], sample_rate: int, block_size: int):
        self.freqs = np.asarray(freqs, dtype=np.float64)
        self.sample_rate = sample_rate
        self.block_size = block_size

        self.ks = np.round(block_size * self.freqs / sample_rate).astype(int)
        self.omegas = (2.0 * np.pi * self.ks) / block_size
        self.coeffs = 2.0 * np.cos(self.omegas)

    def power(self, block: np.ndarray) -> np.ndarray:
        s_prev = np.zeros_like(self.freqs, dtype=np.float64)
        s_prev2 = np.zeros_like(self.freqs, dtype=np.float64)
        for x in block:
            s = x + self.coeffs * s_prev - s_prev2
            s_prev2 = s_prev
            s_prev = s
        power = s_prev2**2 + s_prev**2 - self.coeffs * s_prev * s_prev2
        return power


@dataclass
class DetectionEvent:
    pair: TonePair
    timestamp_ms: int


@dataclass
class DetectionDebugInfo:
    peak_freq_hz: float
    snr_db: float
    best_pair_name: str
    best_pair_delta_hz: float


class TonePairState:
    def __init__(self, pair: TonePair, frame_ms: int):
        self.pair = pair
        self.frame_ms = frame_ms
        self.state = "idle"
        self.a_accum = 0
        self.b_accum = 0
        self.suppress_until = 0

    def reset(self):
        self.state = "idle"
        self.a_accum = 0
        self.b_accum = 0

    def update(self, freq_hit: float | None, snr_db: float, now_ms: int) -> List[DetectionEvent]:
        events: List[DetectionEvent] = []
        if now_ms < self.suppress_until:
            return events

        matches_a = (
            freq_hit is not None
            and abs(freq_hit - self.pair.tone_a_hz) / self.pair.tone_a_hz * 100
            <= self.pair.tolerance_pct
            and snr_db >= self.pair.min_snr_db
        )
        matches_b = (
            freq_hit is not None
            and abs(freq_hit - self.pair.tone_b_hz) / self.pair.tone_b_hz * 100
            <= self.pair.tolerance_pct
            and snr_db >= self.pair.min_snr_db
        )

        if self.state == "idle":
            if matches_a:
                self.a_accum += self.frame_ms
                if self.a_accum >= self.pair.tone_a_ms:
                    self.state = "wait_b"
                    self.b_accum = 0
            else:
                self.a_accum = 0
        elif self.state == "wait_b":
            if matches_b:
                self.b_accum += self.frame_ms
                if self.b_accum >= self.pair.tone_b_ms:
                    events.append(DetectionEvent(self.pair, now_ms))
                    self.state = "idle"
                    self.a_accum = 0
                    self.b_accum = 0
                    self.suppress_until = now_ms + self.pair.action.repeat_suppression_ms
            elif matches_a:
                # still in A, hold
                self.a_accum = min(self.a_accum + self.frame_ms, self.pair.tone_a_ms)
            else:
                self.reset()
        return events


class DetectorEngine:
    """Central detector that maps audio blocks to tone pair events."""

    def __init__(self, config: ServiceConfig):
        self.cfg = config
        self.frame_ms = config.audio.frame_ms
        unique_freqs = sorted(
            {pair.tone_a_hz for pair in config.tone_pairs} | {pair.tone_b_hz for pair in config.tone_pairs}
        )
        self.bank = GoertzelBank(unique_freqs, config.audio.sample_rate, config.frame_samples)
        self.states = [TonePairState(pair, self.frame_ms) for pair in config.tone_pairs]

    def _analyze_block(
        self, samples: np.ndarray, timestamp_ms: int | None = None, *, update_states: bool = True
    ) -> tuple[List[DetectionEvent], DetectionDebugInfo]:
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        block = samples.astype(np.float64)
        powers = self.bank.power(block)
        peak_idx = int(np.argmax(powers))
        if len(powers) > 1:
            other_powers = np.delete(powers, peak_idx)
            noise_floor = np.median(other_powers) + 1e-12
        else:
            noise_floor = 1e-12
        peak_freq = self.bank.freqs[peak_idx]
        peak_power = powers[peak_idx]
        snr_db = db10(peak_power / noise_floor)
        best_pair = min(
            self.cfg.tone_pairs,
            key=lambda pair: min(abs(peak_freq - pair.tone_a_hz), abs(peak_freq - pair.tone_b_hz)),
        )
        best_pair_delta_hz = min(abs(peak_freq - best_pair.tone_a_hz), abs(peak_freq - best_pair.tone_b_hz))

        events: List[DetectionEvent] = []
        if update_states:
            for state in self.states:
                events.extend(state.update(peak_freq, snr_db, timestamp_ms))
        debug = DetectionDebugInfo(
            peak_freq_hz=float(peak_freq),
            snr_db=float(snr_db),
            best_pair_name=best_pair.name,
            best_pair_delta_hz=float(best_pair_delta_hz),
        )
        return events, debug

    def process_block(self, samples: np.ndarray, timestamp_ms: int | None = None) -> List[DetectionEvent]:
        events, _ = self._analyze_block(samples, timestamp_ms)
        return events

    def debug_block(self, samples: np.ndarray, timestamp_ms: int | None = None) -> DetectionDebugInfo:
        _, debug = self._analyze_block(samples, timestamp_ms, update_states=False)
        return debug


def chunk_samples(samples: np.ndarray, frame_samples: int) -> Iterable[np.ndarray]:
    for idx in range(0, len(samples), frame_samples):
        chunk = samples[idx : idx + frame_samples]
        if len(chunk) == frame_samples:
            yield chunk
