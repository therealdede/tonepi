import sys
from queue import Queue

import numpy as np
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from qcii_detector import cli
from qcii_detector.audio import AudioStreamer
from qcii_detector.config import AudioConfig, LoggingConfig, ServiceConfig, ToneAction, TonePair
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


def test_config_requires_at_least_one_tone_pair():
    with pytest.raises(ValidationError, match="at least one tone pair is required"):
        ServiceConfig(
            audio=AudioConfig(sample_rate=8000, frame_ms=100),
            logging=LoggingConfig(level="WARNING"),
            tone_pairs=[],
        )


def test_audio_streamer_restarts_cleanly(monkeypatch):
    class FakeStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeSoundDevice:
        InputStream = FakeStream

        @staticmethod
        def sleep(_ms):
            return None

    monkeypatch.setitem(sys.modules, "sounddevice", FakeSoundDevice)

    streamer = AudioStreamer(AudioConfig(sample_rate=8000, frame_ms=100), 800, Queue(maxsize=1))
    streamer.start()
    assert streamer.thread is not None
    streamer.stop()
    assert streamer.thread is None
    streamer.start()
    assert streamer.thread is not None
    streamer.stop()
    sys.modules.pop("sounddevice", None)


def test_audio_streamer_raises_when_sounddevice_missing(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("missing for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    streamer = AudioStreamer(AudioConfig(sample_rate=8000, frame_ms=100), 800, Queue(maxsize=1))
    with pytest.raises(RuntimeError, match="sounddevice unavailable"):
        streamer.start()


def test_audio_streamer_drops_frames_when_queue_is_full(monkeypatch):
    class FakeSoundDevice:
        def __init__(self):
            self.callback = None

        class InputStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                self.callback(np.ones((800, 1), dtype=np.float32), 800, None, None)
                self.callback(np.ones((800, 1), dtype=np.float32), 800, None, None)
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        @staticmethod
        def sleep(_ms):
            return None

    monkeypatch.setitem(sys.modules, "sounddevice", FakeSoundDevice)

    q = Queue(maxsize=1)
    streamer = AudioStreamer(AudioConfig(sample_rate=8000, frame_ms=100), 800, q)
    streamer.start()
    streamer.stop()

    assert q.qsize() == 1
    assert streamer.dropped_frames == 1
    sys.modules.pop("sounddevice", None)


def test_cli_without_subcommand_launches_tui(monkeypatch):
    calls: list[str] = []

    def fake_run_tui(path):
        calls.append(path)

    monkeypatch.setattr(cli, "run_tui", fake_run_tui)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--config", "/tmp/qcii.yaml"])

    assert result.exit_code == 0
    assert calls == ["/tmp/qcii.yaml"]


def test_list_tones_rejects_unknown_choice():
    runner = CliRunner()
    result = runner.invoke(cli.main, ["list-tones", "--set", "bogus"])

    assert result.exit_code != 0
    assert "Invalid value for '--set'" in result.output
