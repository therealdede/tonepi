from __future__ import annotations

import logging
import threading
import time
from typing import Dict

from .config import ToneAction

LOG = logging.getLogger(__name__)


class RelayDriver:
    """Drive GPIO pins to energize relays; gracefully degrades if gpiozero is unavailable."""

    def __init__(self):
        try:
            import gpiozero  # type: ignore

            self.gpiozero = gpiozero
        except Exception as exc:  # pragma: no cover
            LOG.warning("gpiozero unavailable, using mock driver: %s", exc)
            self.gpiozero = None
        self.devices: Dict[int, object] = {}
        self.lock = threading.Lock()
        self.last_activation: Dict[int, float] = {}

    def _get_device(self, pin: int):
        if self.gpiozero is None:
            return None
        if pin not in self.devices:
            self.devices[pin] = self.gpiozero.OutputDevice(
                pin, active_high=True, initial_value=False
            )
        return self.devices[pin]

    def activate(self, action: ToneAction):
        with self.lock:
            now = time.time() * 1000
            last = self.last_activation.get(action.gpio_pin, 0)
            if now - last < action.rearm_ms:
                LOG.info(
                    "Skip activation on pin %s: rearm window",
                    action.gpio_pin,
                )
                return
            self.last_activation[action.gpio_pin] = now

        device = self._get_device(action.gpio_pin)
        LOG.info(
            "Activating relay pin=%s for %sms (%s)",
            action.gpio_pin,
            action.hold_ms,
            action.name or "unnamed",
        )
        if device is None:
            LOG.info("Mock activation (no gpiozero)")
            threading.Timer(action.hold_ms / 1000.0, lambda: None).start()
            return

        def pulse():
            device.on()
            time.sleep(action.hold_ms / 1000.0)
            device.off()

        threading.Thread(target=pulse, daemon=True).start()
