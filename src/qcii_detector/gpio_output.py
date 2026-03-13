from __future__ import annotations

import logging
import threading
import time
from typing import Dict

from .config import ToneAction

LOG = logging.getLogger(__name__)
INVALID_DEVICE = object()


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
        self.device_polarity: Dict[int, bool] = {}
        self.invalid_pins: set[int] = set()
        self.lock = threading.Lock()
        self.last_activation: Dict[int, float] = {}

    def _build_device(self, action: ToneAction):
        return self.gpiozero.OutputDevice(
            action.gpio_pin,
            active_high=action.active_high,
            initial_value=False,
        )

    def _get_device(self, action: ToneAction):
        if self.gpiozero is None:
            return None
        pin = action.gpio_pin
        if pin in self.invalid_pins:
            return INVALID_DEVICE
        current = self.devices.get(pin)
        if current is not None and self.device_polarity.get(pin) != action.active_high:
            close = getattr(current, "close", None)
            if callable(close):
                close()
            current = None
            self.devices.pop(pin, None)
            self.device_polarity.pop(pin, None)
        if current is None:
            try:
                self.devices[pin] = self._build_device(action)
                self.device_polarity[pin] = action.active_high
            except Exception as exc:
                self.invalid_pins.add(pin)
                LOG.error("Invalid GPIO pin %s; skipping relay activation: %s", pin, exc)
                return INVALID_DEVICE
            return self.devices[pin]
        return current

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

        device = self._get_device(action)
        LOG.info(
            "Activating relay pin=%s for %sms (%s, active_high=%s)",
            action.gpio_pin,
            action.hold_ms,
            action.name or "unnamed",
            action.active_high,
        )
        if device is INVALID_DEVICE:
            return
        if device is None:
            LOG.info("Mock activation (no gpiozero)")
            threading.Timer(action.hold_ms / 1000.0, lambda: None).start()
            return

        def pulse():
            try:
                device.on()
                time.sleep(action.hold_ms / 1000.0)
                device.off()
            except Exception as exc:
                LOG.error("GPIO activation failed on pin %s: %s", action.gpio_pin, exc)

        threading.Thread(target=pulse, daemon=True).start()
