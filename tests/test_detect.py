import numpy as np

from qcii_detector.config import ServiceConfig, ToneAction, TonePair, AudioConfig, LoggingConfig
from qcii_detector.detect import DetectorEngine, chunk_samples


def make_pair_wave(tone_a, tone_b, sample_rate=8000, tone_ms=600, silence_ms=0, amplitude=0.8):
    def tone(freq_hz, duration_ms):
        t = np.arange(int(sample_rate * duration_ms / 1000)) / sample_rate
        return amplitude * np.sin(2 * np.pi * freq_hz * t)

    a = tone(tone_a, tone_ms)
    gap = np.zeros(int(sample_rate * silence_ms / 1000))
    b = tone(tone_b, tone_ms)
    return np.concatenate([a, gap, b]).astype(np.float64)


def build_config(tone_a, tone_b):
    pair = TonePair(
        name="Test",
        tone_a_hz=tone_a,
        tone_b_hz=tone_b,
        tone_a_ms=500,
        tone_b_ms=500,
        tolerance_pct=1.5,
        min_snr_db=6.0,
        action=ToneAction(gpio_pin=17),
    )
    cfg = ServiceConfig(
        audio=AudioConfig(sample_rate=8000, frame_ms=100),
        logging=LoggingConfig(level="WARNING"),
        tone_pairs=[pair],
    )
    return cfg


def test_detects_pair():
    tone_a = 707.3
    tone_b = 953.7
    cfg = build_config(tone_a, tone_b)
    engine = DetectorEngine(cfg)

    wave = make_pair_wave(tone_a, tone_b, sample_rate=cfg.audio.sample_rate)
    detections = []
    for idx, chunk in enumerate(chunk_samples(wave, cfg.frame_samples)):
        ts = idx * cfg.audio.frame_ms
        detections.extend(engine.process_block(chunk, ts))
    assert len(detections) == 1
    assert detections[0].pair.name == "Test"


def test_no_false_positive_with_noise():
    tone_a = 707.3
    tone_b = 953.7
    cfg = build_config(tone_a, tone_b)
    engine = DetectorEngine(cfg)

    noise = np.random.normal(0, 0.1, cfg.frame_samples * 10).astype(np.float64)
    detections = []
    for idx, chunk in enumerate(chunk_samples(noise, cfg.frame_samples)):
        ts = idx * cfg.audio.frame_ms
        detections.extend(engine.process_block(chunk, ts))
    assert len(detections) == 0
