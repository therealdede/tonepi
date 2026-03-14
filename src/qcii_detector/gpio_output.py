from __future__ import annotations

import logging
import threading
import time
from importlib import metadata
from typing import Dict, Optional

from .config import ToneAction

LOG = logging.getLogger(__name__)
INVALID_DEVICE = object()


class RelayDriver:
    """Drive GPIO pins to energize relays; gracefully degrades if gpiozero is unavailable."""

    def __init__(self):
        self.backend_warning: Optional[str] = None
        self.last_error: Optional[str] = None
        try:
            import gpiozero  # type: ignore

            self.gpiozero = gpiozero
            self._log_gpiozero_details()
        except Exception as exc:  # pragma: no cover
            self.last_error = f"gpiozero unavailable: {exc}"
            LOG.warning("gpiozero unavailable, using mock driver: %s", exc)
            self.gpiozero = None
        self.pin_factory: Optional[object] = None
        if self.gpiozero is not None:
            self.pin_factory = self._prefer_lgpio_factory()
        self.devices: Dict[int, object] = {}
        self.device_polarity: Dict[int, bool] = {}
        self.invalid_pins: set[int] = set()
        self.lock = threading.Lock()
        self.last_activation: Dict[int, float] = {}

    def _log_gpiozero_details(self) -> None:
        version = getattr(self.gpiozero, "__version__", "unknown")
        try:
            version = metadata.version("gpiozero")
        except metadata.PackageNotFoundError:
            pass
        except Exception:
            pass

        location = getattr(self.gpiozero, "__file__", "unknown location")
        LOG.info("Using gpiozero %s from %s", version, location)

        major = _parse_major_version(version)
        if major is not None and major < 2:
            self.backend_warning = (
                f"gpiozero {version} detected from {location}. "
                "Raspberry Pi 5 support requires gpiozero 2.x; "
                "reinstall the project inside the virtualenv to restore live GPIO output."
            )
            LOG.warning(
                "gpiozero %s detected from %s. Raspberry Pi 5 support requires gpiozero 2.x; "
                "reinstall the project inside the virtualenv to restore live GPIO output.",
                version,
                location,
            )

    def _prefer_lgpio_factory(self) -> Optional[object]:
        try:
            from gpiozero import Device  # type: ignore
            from gpiozero.pins.lgpio import LGPIOFactory  # type: ignore

            if not isinstance(Device.pin_factory, LGPIOFactory):
                Device.pin_factory = LGPIOFactory()
            LOG.info("Using gpiozero pin factory %s", type(Device.pin_factory).__name__)
            return Device.pin_factory
        except Exception as exc:
            self.backend_warning = (
                "Unable to initialize gpiozero LGPIOFactory. "
                "On Raspberry Pi 5, install python3-lgpio and ensure the user can access /dev/gpiochip*."
            )
            LOG.warning(
                "Unable to initialize gpiozero LGPIOFactory: %s. "
                "On Raspberry Pi 5, install python3-lgpio and ensure the user can access /dev/gpiochip*.",
                exc,
            )
            return None

    def describe_backend(self) -> str:
        if self.gpiozero is None:
            return f"GPIO backend: mock ({self.last_error or 'gpiozero unavailable'})"

        version = "unknown"
        try:
            version = metadata.version("gpiozero")
        except Exception:
            version = getattr(self.gpiozero, "__version__", "unknown")

        location = getattr(self.gpiozero, "__file__", "unknown location")
        pin_factory = type(self.pin_factory).__name__ if self.pin_factory is not None else "auto/unknown"
        text = f"GPIO backend: gpiozero {version} from {location}; pin factory {pin_factory}"
        if self.backend_warning:
            text = f"{text}; warning: {self.backend_warning}"
        if self.last_error:
            text = f"{text}; last error: {self.last_error}"
        return text

    def close(self) -> None:
        for pin, device in list(self.devices.items()):
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    LOG.warning("Failed to close GPIO device on pin %s: %s", pin, exc)
        self.devices.clear()
        self.device_polarity.clear()
        self.last_activation.clear()

    def _build_device(self, action: ToneAction):
        kwargs = {
            "active_high": action.active_high,
            "initial_value": False,
        }
        if self.pin_factory is not None:
            kwargs["pin_factory"] = self.pin_factory
        try:
            return self.gpiozero.OutputDevice(action.gpio_pin, **kwargs)
        except TypeError as exc:
            # Some fake/minimal backends used in tests or older backends don't
            # accept an explicit pin_factory keyword. Retry without it.
            if "pin_factory" in kwargs and "pin_factory" in str(exc):
                kwargs.pop("pin_factory", None)
                return self.gpiozero.OutputDevice(action.gpio_pin, **kwargs)
            raise

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
                self.last_error = None
            except Exception as exc:
                self.invalid_pins.add(pin)
                hint = _pin_error_hint(exc)
                message = f"Invalid GPIO pin {pin}; skipping relay activation: {exc}"
                if hint:
                    message = f"{message}. {hint}"
                self.last_error = message
                LOG.error(message)
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
                self.last_error = None
            except Exception as exc:
                self.last_error = f"GPIO activation failed on pin {action.gpio_pin}: {exc}"
                LOG.error("GPIO activation failed on pin %s: %s", action.gpio_pin, exc)

        threading.Thread(target=pulse, daemon=True).start()


def _parse_major_version(version: str) -> int | None:
    try:
        return int(str(version).split(".", 1)[0])
    except Exception:
        return None


def _pin_error_hint(exc: Exception) -> str:
    message = str(exc).lower()
    if "already in use" in message:
        return "another RelayDriver instance still owns that pin; stop detection or restart the TUI to release it"
    if "default pin factory" in message or "badpinfactory" in message:
        return (
            "gpiozero could not initialize a GPIO backend; install python3-lgpio "
            "and make sure the current user can access /dev/gpiochip*"
        )
    if "gpiochip" in message or "permission denied" in message:
        return "check /dev/gpiochip* access and membership in the gpio group"
    return ""
