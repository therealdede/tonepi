from __future__ import annotations

import logging
from queue import Full, Queue
from threading import Event, Thread
from typing import Optional

import numpy as np

from .config import AudioConfig

LOG = logging.getLogger(__name__)


class AudioStreamer:
    """Capture audio frames from ALSA using sounddevice and push to a queue."""

    def __init__(self, cfg: AudioConfig, frame_samples: int, target_queue: Queue):
        self.cfg = cfg
        self.frame_samples = frame_samples
        self.q = target_queue
        self.stop_event = Event()
        self.thread: Optional[Thread] = None
        self.dropped_frames = 0

    def start(self):
        if self.thread:
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._run, name="audio-streamer", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None

    def _run(self):
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - import error surface only on target
            LOG.error("sounddevice unavailable: %s", exc)
            return

        def callback(indata, frames, time_info, status):
            if status:
                LOG.warning("Audio status: %s", status)
            mono = indata[:, 0] if indata.ndim > 1 else indata
            try:
                self.q.put_nowait(np.copy(mono))
            except Full:
                self.dropped_frames += 1
                if self.dropped_frames % 50 == 1:
                    LOG.warning(
                        "Audio queue full; dropping frames (dropped=%d)",
                        self.dropped_frames,
                    )

        with sd.InputStream(
            samplerate=self.cfg.sample_rate,
            channels=1,
            blocksize=self.frame_samples,
            device=self.cfg.device,
            dtype="float32",
            callback=callback,
        ):
            LOG.info("Audio capture started (device=%s)", self.cfg.device or "default")
            while not self.stop_event.is_set():
                sd.sleep(200)
        LOG.info("Audio capture stopped")
