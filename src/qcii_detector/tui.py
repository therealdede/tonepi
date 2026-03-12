from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Log, Static

from .config import ServiceConfig, ToneAction, TonePair, load_config
from .gpio_output import RelayDriver

LOG = logging.getLogger(__name__)


class ToneEditScreen(ModalScreen[Optional[TonePair]]):
    """Modal editor for a tone pair."""

    def __init__(self, tone: Optional[TonePair] = None):
        super().__init__()
        self.tone = tone

    def compose(self) -> ComposeResult:
        tone = self.tone
        yield Static("Edit Tone Pair", classes="title")
        self.inputs = {
            "name": Input(value=tone.name if tone else "", placeholder="Name"),
            "tone_a_hz": Input(value=str(tone.tone_a_hz) if tone else "", placeholder="Tone A (Hz)"),
            "tone_b_hz": Input(value=str(tone.tone_b_hz) if tone else "", placeholder="Tone B (Hz)"),
            "tone_a_ms": Input(value=str(tone.tone_a_ms if tone else 600), placeholder="Tone A ms"),
            "tone_b_ms": Input(value=str(tone.tone_b_ms if tone else 600), placeholder="Tone B ms"),
            "tolerance_pct": Input(value=str(tone.tolerance_pct if tone else 1.5), placeholder="Tolerance %"),
            "min_snr_db": Input(value=str(tone.min_snr_db if tone else 6.0), placeholder="Min SNR dB"),
            "gpio_pin": Input(value=str(tone.action.gpio_pin if tone else ""), placeholder="GPIO pin (BCM)"),
            "hold_ms": Input(value=str(tone.action.hold_ms if tone else 1500), placeholder="Hold ms"),
            "rearm_ms": Input(value=str(tone.action.rearm_ms if tone else 2000), placeholder="Rearm ms"),
            "repeat_suppression_ms": Input(
                value=str(tone.action.repeat_suppression_ms if tone else 3000), placeholder="Repeat suppress ms"
            ),
            "action_name": Input(value=str(tone.action.name) if tone and tone.action.name else "", placeholder="Action name"),
        }
        form = Vertical(*self.inputs.values(), classes="form")
        buttons = Horizontal(
            Button("Cancel", variant="default", id="cancel"),
            Button("Save", variant="primary", id="save"),
            classes="buttons",
        )
        yield form
        yield buttons

    class Submit(Message):
        def __init__(self, tone: Optional[TonePair]):
            super().__init__()
            self.tone = tone

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            tone = TonePair(
                name=self.inputs["name"].value.strip() or "Tone",
                tone_a_hz=float(self.inputs["tone_a_hz"].value),
                tone_b_hz=float(self.inputs["tone_b_hz"].value),
                tone_a_ms=int(self.inputs["tone_a_ms"].value),
                tone_b_ms=int(self.inputs["tone_b_ms"].value),
                tolerance_pct=float(self.inputs["tolerance_pct"].value),
                min_snr_db=float(self.inputs["min_snr_db"].value),
                action=ToneAction(
                    gpio_pin=int(self.inputs["gpio_pin"].value),
                    hold_ms=int(self.inputs["hold_ms"].value),
                    rearm_ms=int(self.inputs["rearm_ms"].value),
                    repeat_suppression_ms=int(self.inputs["repeat_suppression_ms"].value),
                    name=self.inputs["action_name"].value or None,
                ),
            )
        except Exception as exc:
            self.app.push_screen(MessageScreen(f"Invalid input: {exc}"))
            return
        self.dismiss(tone)


class MessageScreen(ModalScreen[None]):
    """Simple modal message."""

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        yield Static(self.text, classes="message")
        yield Button("OK", id="ok", variant="primary")

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)


class ConfigManager:
    def __init__(self, path: Path):
        self.path = path
        self.config = load_config(path)

    def save(self) -> None:
        data = self.config.model_dump()
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

    def validate(self) -> Optional[str]:
        try:
            ServiceConfig.model_validate(self.config.model_dump())
            return None
        except ValidationError as exc:
            return str(exc)


class ToneTable(DataTable):
    def add_pair(self, idx: int, pair: TonePair):
        self.add_row(
            pair.name,
            f"{pair.tone_a_hz:.1f}",
            f"{pair.tone_b_hz:.1f}",
            str(pair.action.gpio_pin),
            str(pair.action.hold_ms),
            key=idx,
        )


class QCIIConfigApp(App):
    CSS_PATH = None
    BINDINGS = [("q", "quit", "Quit"), ("s", "save", "Save"), ("r", "reload", "Reload"), ("p", "pulse", "Test pulse")]
    config_path: reactive[Path] = reactive(Path("/etc/qcii.yaml"))

    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.manager = ConfigManager(config_path)
        self.relay = RelayDriver()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Horizontal():
            with Vertical(id="left"):
                yield Static(f"Config: {self.config_path}", id="path")
                self.tone_table = ToneTable(id="tones")
                self.tone_table.add_columns("Name", "Tone A", "Tone B", "GPIO", "Hold ms")
                self.refresh_tones()
                yield self.tone_table
                with Horizontal():
                    yield Button("Add", id="add")
                    yield Button("Edit", id="edit")
                    yield Button("Delete", id="delete")
            with Vertical(id="right"):
                yield Static("Audio settings", classes="section")
                self.audio_rate = Input(value=str(self.manager.config.audio.sample_rate), placeholder="Sample rate")
                self.audio_frame = Input(value=str(self.manager.config.audio.frame_ms), placeholder="Frame ms")
                self.audio_device = Input(
                    value="" if self.manager.config.audio.device is None else str(self.manager.config.audio.device),
                    placeholder="ALSA device",
                )
                yield self.audio_rate
                yield self.audio_frame
                yield self.audio_device
                yield Static("Logging", classes="section")
                self.log_level = Input(value=self.manager.config.logging.level, placeholder="Level INFO/DEBUG")
                self.log_file = Input(value=self.manager.config.logging.file or "", placeholder="Log file path")
                yield self.log_level
                yield self.log_file
                yield Button("Test Pulse", id="pulse")
                yield Button("Save", id="save", variant="primary")
                yield Button("Reload from disk", id="reload")
                self.status = Log(highlight=False, max_lines=200)
                yield self.status

    def refresh_tones(self):
        self.tone_table.clear()
        for idx, pair in enumerate(self.manager.config.tone_pairs):
            self.tone_table.add_pair(idx, pair)

    def action_save(self):
        self.save_config()

    def action_reload(self):
        self.reload_config()

    def action_pulse(self):
        self.test_pulse()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "add":
                self.push_screen(ToneEditScreen(), self._add_tone_callback)
            case "edit":
                self.edit_selected()
            case "delete":
                self.delete_selected()
            case "save":
                self.save_config()
            case "reload":
                self.reload_config()
            case "pulse":
                self.test_pulse()

    def _selected_index(self) -> Optional[int]:
        row = self.tone_table.cursor_row
        if row is None:
            return None
        try:
            return int(self.tone_table.get_row_at(row))
        except Exception:
            return None

    def edit_selected(self):
        idx = self._selected_index()
        if idx is None:
            self.push_screen(MessageScreen("No tone selected"))
            return
        tone = self.manager.config.tone_pairs[idx]
        self.push_screen(ToneEditScreen(tone), lambda result: self._update_tone(idx, result))

    def _add_tone_callback(self, result: Optional[TonePair]):
        if result:
            self.manager.config.tone_pairs.append(result)
            self.refresh_tones()

    def _update_tone(self, idx: int, result: Optional[TonePair]):
        if result:
            self.manager.config.tone_pairs[idx] = result
            self.refresh_tones()

    def delete_selected(self):
        idx = self._selected_index()
        if idx is None:
            self.push_screen(MessageScreen("No tone selected"))
            return
        del self.manager.config.tone_pairs[idx]
        self.refresh_tones()

    def reload_config(self):
        self.manager = ConfigManager(self.config_path)
        self.refresh_tones()
        self.audio_rate.value = str(self.manager.config.audio.sample_rate)
        self.audio_frame.value = str(self.manager.config.audio.frame_ms)
        self.audio_device.value = "" if self.manager.config.audio.device is None else str(self.manager.config.audio.device)
        self.log_level.value = self.manager.config.logging.level
        self.log_file.value = self.manager.config.logging.file or ""
        self._log_status("Reloaded from disk")

    def save_config(self):
        cfg = self.manager.config
        try:
            cfg.audio.sample_rate = int(self.audio_rate.value)
            cfg.audio.frame_ms = int(self.audio_frame.value)
            cfg.audio.device = self.audio_device.value or None
            cfg.logging.level = self.log_level.value or "INFO"
            cfg.logging.file = self.log_file.value or None
            err = self.manager.validate()
            if err:
                self.push_screen(MessageScreen(f"Validation error:\n{err}"))
                return
            self.manager.save()
            self._log_status(f"Saved to {self.config_path}")
        except Exception as exc:
            self.push_screen(MessageScreen(f"Save failed: {exc}"))

    def test_pulse(self):
        idx = self._selected_index()
        if idx is None:
            self.push_screen(MessageScreen("Select a tone row first"))
            return
        action = self.manager.config.tone_pairs[idx].action
        self.relay.activate(action)
        self._log_status(f"Pulse on GPIO {action.gpio_pin}")

    async def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            await self.action_quit()

    def _log_status(self, msg: str) -> None:
        try:
            self.status.write_line(msg)
        except Exception:
            # fallback for older/newer API
            try:
                self.status.write(msg)
            except Exception:
                pass


def run_tui(config_path: str | Path):
    path = Path(config_path)
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    app = QCIIConfigApp(path)
    app.run()
