from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_CONFIG_PATH = "config/qcii.yaml"
DEFAULT_LOG_PATH = "logs/qcii.log"


class ToneAction(BaseModel):
    gpio_pin: int = Field(..., description="GPIO pin number (BCM) driving the relay")
    hold_ms: int = Field(1500, ge=10, description="Relay energized duration")
    rearm_ms: int = Field(2000, ge=0, description="Minimum time before next activation")
    repeat_suppression_ms: int = Field(
        3000, ge=0, description="Ignore repeated detections within this window"
    )
    name: str | None = Field(None, description="Optional output name")


class TonePair(BaseModel):
    name: str
    tone_a_hz: float
    tone_b_hz: float
    tone_a_ms: int = Field(600, ge=100)
    tone_b_ms: int = Field(600, ge=100)
    tolerance_pct: float = Field(1.5, ge=0.1, le=5.0)
    min_snr_db: float = Field(6.0, description="Minimum SNR for detection")
    action: ToneAction

    @field_validator("tone_a_hz", "tone_b_hz")
    @classmethod
    def positive_frequency(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("frequency must be positive")
        return v


class LoggingConfig(BaseModel):
    level: str = Field("INFO")
    file: Optional[str] = Field(DEFAULT_LOG_PATH, description="Optional log file path")
    max_bytes: int = Field(5_000_000, ge=1000)
    backup_count: int = Field(3, ge=0)


class AudioConfig(BaseModel):
    sample_rate: int = Field(8000, ge=4000, le=48000)
    frame_ms: int = Field(100, ge=10, le=500)
    bandpass_hz: tuple[int, int] = Field((300, 3000))
    device: Optional[str | int] = Field(None, description="ALSA device identifier")


class ServiceConfig(BaseModel):
    audio: AudioConfig = AudioConfig()
    logging: LoggingConfig = LoggingConfig()
    tone_pairs: List[TonePair]

    @field_validator("tone_pairs")
    @classmethod
    def require_tone_pairs(cls, value: List[TonePair]) -> List[TonePair]:
        if not value:
            raise ValueError("at least one tone pair is required")
        return value

    @property
    def frame_samples(self) -> int:
        return int(self.audio.sample_rate * self.audio.frame_ms / 1000)


def load_config(path: str | Path) -> ServiceConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    try:
        return ServiceConfig.model_validate(data)
    except ValidationError as exc:
        raise SystemExit(f"Invalid configuration: {exc}") from exc
