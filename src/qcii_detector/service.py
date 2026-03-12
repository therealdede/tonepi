from __future__ import annotations

import logging
import queue
import signal
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .audio import AudioStreamer
from .config import ServiceConfig, load_config
from .detect import DetectorEngine
from .gpio_output import RelayDriver
from .logging_utils import configure_logging

LOG = logging.getLogger(__name__)


class QCIIService:
    def __init__(self, cfg: ServiceConfig):
        self.cfg = cfg
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=50)
        self.detector = DetectorEngine(cfg)
        self.relay = RelayDriver()
        self.audio = AudioStreamer(cfg.audio, cfg.frame_samples, self.audio_queue)
        self._stop_event = threading.Event()

    def start(self):
        LOG.info("Starting QCII service with %d tone pairs", len(self.cfg.tone_pairs))
        self.audio.start()
        try:
            self._loop()
        finally:
            self.stop()

    def stop(self):
        self._stop_event.set()
        self.audio.stop()

    def _loop(self):
        try:
            while not self._stop_event.is_set():
                try:
                    block = self.audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                timestamp = int(time.time() * 1000)
                events = self.detector.process_block(block, timestamp)
                for ev in events:
                    self.relay.activate(ev.pair.action)
def run_service(config_path: str | Path):
    cfg = load_config(config_path)
    configure_logging(cfg.logging)

    service = QCIIService(cfg)

    def handle_signal(signum, frame):
        LOG.info("Signal %s received, stopping service", signum)
        service.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    service.start()
