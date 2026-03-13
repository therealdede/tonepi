from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


PREFERRED_USB_KEYWORDS = (
    "sabrent",
    "usb audio",
    "usb pnp",
    "audio adapter",
    "usb",
)


@dataclass
class AudioDeviceInfo:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0


def _import_sounddevice():
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"sounddevice unavailable: {exc}") from exc
    return sd


def list_audio_devices() -> list[AudioDeviceInfo]:
    sd = _import_sounddevice()
    devices: list[AudioDeviceInfo] = []
    for index, raw in enumerate(sd.query_devices()):
        devices.append(
            AudioDeviceInfo(
                index=index,
                name=str(raw["name"]),
                max_input_channels=int(raw["max_input_channels"]),
                max_output_channels=int(raw["max_output_channels"]),
                default_samplerate=float(raw["default_samplerate"]),
            )
        )
    return devices


def resolve_input_device(configured: str | int | None) -> str | int | None:
    if configured not in (None, ""):
        return configured
    selected = auto_select_input_device()
    return selected.index if selected else None


def resolve_input_device_info(configured: str | int | None) -> AudioDeviceInfo | None:
    devices = list_audio_devices()
    selected = resolve_input_device(configured)
    if selected is None:
        return None
    if isinstance(selected, int):
        for device in devices:
            if device.index == selected:
                return device
        return None
    selected_name = str(selected)
    for device in devices:
        if device.name == selected_name:
            return device
    return None


def resolve_sample_rate(configured_device: str | int | None, configured_rate: int | None) -> int:
    if configured_rate:
        return int(configured_rate)
    device = resolve_input_device_info(configured_device)
    if device and device.default_samplerate > 0:
        return int(round(device.default_samplerate))
    return 44100


def auto_select_input_device() -> AudioDeviceInfo | None:
    candidates = [device for device in list_audio_devices() if device.is_input]
    if not candidates:
        return None

    keyword_match = _pick_keyword_match(candidates, PREFERRED_USB_KEYWORDS)
    if keyword_match:
        return keyword_match

    default_input = _default_input_device(candidates)
    if default_input:
        return default_input

    return candidates[0]


def describe_selected_input(configured: str | int | None) -> str:
    selected = resolve_input_device(configured)
    if selected is None:
        return "system default input"

    devices = list_audio_devices()
    if isinstance(selected, int):
        for device in devices:
            if device.index == selected:
                return f"{device.name} (index {device.index})"
        return f"device index {selected}"

    return str(selected)


def _pick_keyword_match(
    devices: Iterable[AudioDeviceInfo], keywords: tuple[str, ...]
) -> AudioDeviceInfo | None:
    ranked: list[tuple[int, AudioDeviceInfo]] = []
    for device in devices:
        name = device.name.lower()
        score = 0
        for keyword in keywords:
            if keyword in name:
                score = max(score, len(keyword))
        if score:
            ranked.append((score, device))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _default_input_device(candidates: list[AudioDeviceInfo]) -> AudioDeviceInfo | None:
    sd = _import_sounddevice()
    default_input, _default_output = sd.default.device
    if default_input is None or default_input < 0:
        return None
    for device in candidates:
        if device.index == default_input:
            return device
    return None
