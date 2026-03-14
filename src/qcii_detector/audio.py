from __future__ import annotations

import logging
from queue import Full, Queue
from threading import Event, Thread
from typing import Optional

import numpy as np

from .audio_devices import describe_selected_input, resolve_input_device
from .config import AudioConfig

LOG = logging.getLogger(__name__)


class AudioStreamer:
    """Capture audio frames from ALSA using sounddevice and push to a queue."""

    def __init__(self, cfg: AudioConfig, frame_samples: int, target_queue: Queue):
        self.cfg = cfg
        self.frame_samples = frame_samples
        self.q = target_queue
        self.stop_event = Event()
        self.started_event = Event()
        self.thread: Optional[Thread] = None
        self.dropped_frames = 0
        self.startup_error: Optional[str] = None
        self.runtime_error: Optional[str] = None

    def start(self):
        if self.thread:
            return
        self.stop_event.clear()
        self.started_event.clear()
        self.startup_error = None
        self.runtime_error = None
        self.thread = Thread(target=self._run, name="audio-streamer", daemon=True)
        self.thread.start()
        self.started_event.wait(timeout=5)
        if self.startup_error:
            self.thread.join(timeout=2)
            self.thread = None
            raise RuntimeError(self.startup_error)
        if not self.started_event.is_set():
            self.stop_event.set()
            self.thread.join(timeout=2)
            self.thread = None
            raise RuntimeError("Audio capture did not start within 5 seconds")

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None

    def health_error(self) -> Optional[str]:
        if self.runtime_error:
            return self.runtime_error
        if self.thread is not None and not self.thread.is_alive() and not self.stop_event.is_set():
            return "audio capture stopped unexpectedly"
        return None

    def _run(self):
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - import error surface only on target
            self.startup_error = f"sounddevice unavailable: {exc}"
            LOG.error(self.startup_error)
            self.started_event.set()
            return

        selected_device = None
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

        try:
            selected_device = resolve_input_device(self.cfg.device)
            with sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=1,
                blocksize=self.frame_samples,
                device=selected_device,
                dtype="float32",
                callback=callback,
            ):
                LOG.info(
                    "Audio capture started (requested=%s, using=%s)",
                    self.cfg.device if self.cfg.device not in (None, "") else "auto",
                    describe_selected_input(selected_device),
                )
                self.started_event.set()
                while not self.stop_event.is_set():
                    sd.sleep(200)
        except Exception as exc:
            message = f"audio stream failed to start: {exc}"
            if self.started_event.is_set():
                self.runtime_error = message
            else:
                self.startup_error = message
            LOG.error(message)
            self.started_event.set()
            return
        LOG.info("Audio capture stopped")
