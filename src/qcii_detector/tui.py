from __future__ import annotations

import logging
import os
import queue
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import numpy as np
import yaml
from pydantic import ValidationError
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Log, Static

from .audio import AudioStreamer
from .audio_devices import resolve_input_device, resolve_sample_rate
from .config import (
    DEFAULT_CONFIG_PATH,
    MAX_ACTION_MS,
    MIN_ACTION_MS,
    ServiceConfig,
    ToneAction,
    TonePair,
    load_config,
)
from .detect import DetectorEngine
from .gpio_output import RelayDriver

LOG = logging.getLogger(__name__)


class ToneEditScreen(ModalScreen[Optional[TonePair]]):
    """Modal editor for a tone pair."""

    CSS = """
    ToneEditScreen {
        align: center middle;
    }
    #tone-editor {
        width: 88;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #tone-form {
        height: 1fr;
    }
    .field-label {
        margin: 1 0 0 0;
    }
    #tone-buttons {
        margin-top: 1;
        height: auto;
    }
    #tone-buttons Button {
        width: 1fr;
        margin-right: 1;
    }
    """

    def __init__(self, tone: Optional[TonePair] = None):
        super().__init__()
        self.tone = tone

    def compose(self) -> ComposeResult:
        tone = self.tone
        self.inputs = {
            "name": Input(value=tone.name if tone else "", placeholder="Station/Group name"),
            "tone_a_hz": Input(value=str(tone.tone_a_hz) if tone else "", placeholder="e.g. 707.3"),
            "tone_b_hz": Input(value=str(tone.tone_b_hz) if tone else "", placeholder="e.g. 953.7"),
            "tone_a_ms": Input(value=str(tone.tone_a_ms if tone else 600), placeholder="e.g. 600"),
            "tone_b_ms": Input(value=str(tone.tone_b_ms if tone else 600), placeholder="e.g. 600"),
            "tolerance_pct": Input(value=str(tone.tolerance_pct if tone else 1.5), placeholder="e.g. 1.5"),
            "min_snr_db": Input(value=str(tone.min_snr_db if tone else 6.0), placeholder="e.g. 6.0"),
            "gpio_pin": Input(value=str(tone.action.gpio_pin if tone else ""), placeholder="BCM number"),
            "active_high": Input(
                value=str(tone.action.active_high).lower() if tone else "true",
                placeholder="true or false",
            ),
            "hold_ms": Input(value=str(tone.action.hold_ms if tone else 1500), placeholder="e.g. 1500"),
            "rearm_ms": Input(value=str(tone.action.rearm_ms if tone else 2000), placeholder="e.g. 2000"),
            "repeat_suppression_ms": Input(
                value=str(tone.action.repeat_suppression_ms if tone else 3000), placeholder="e.g. 3000"
            ),
            "action_name": Input(
                value=str(tone.action.name) if tone and tone.action.name else "",
                placeholder="Optional output label",
            ),
        }
        fields = [
            ("name", "Name"),
            ("tone_a_hz", "Tone A (Hz)"),
            ("tone_b_hz", "Tone B (Hz)"),
            ("tone_a_ms", "Tone A Duration (ms)"),
            ("tone_b_ms", "Tone B Duration (ms)"),
            ("tolerance_pct", "Frequency Tolerance (%)"),
            ("min_snr_db", "Minimum SNR (dB)"),
            ("gpio_pin", "GPIO Pin (BCM)"),
            ("active_high", "Relay Active High"),
            ("hold_ms", "Relay Hold (ms)"),
            ("rearm_ms", "Re-arm Delay (ms)"),
            ("repeat_suppression_ms", "Repeat Suppression (ms)"),
            ("action_name", "Action Name"),
        ]
        with Vertical(id="tone-editor"):
            yield Static("Edit Tone Pair", classes="title")
            with VerticalScroll(id="tone-form"):
                for key, label in fields:
                    yield Label(label, classes="field-label")
                    yield self.inputs[key]
            with Horizontal(id="tone-buttons"):
                yield Button("Cancel", variant="default", id="cancel")
                yield Button("Save", variant="primary", id="save")

    class Submit(Message):
        def __init__(self, tone: Optional[TonePair]):
            super().__init__()
            self.tone = tone

    def _parse_int_field(self, key: str, label: str) -> int:
        raw = self.inputs[key].value.strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{label} must be a whole number") from exc

    def _parse_float_field(self, key: str, label: str) -> float:
        raw = self.inputs[key].value.strip()
        try:
            return float(raw)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number") from exc

    def _parse_bool_field(self, key: str, label: str) -> bool:
        raw = self.inputs[key].value.strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"{label} must be true or false")

    def _validate_ms_field(self, value: int, label: str) -> int:
        if value < MIN_ACTION_MS or value > MAX_ACTION_MS:
            raise ValueError(f"{label} must be between {MIN_ACTION_MS} and {MAX_ACTION_MS} ms")
        return value

    def _collect_ms_field_errors(self) -> list[str]:
        fields = [
            ("tone_a_ms", "Tone A Duration"),
            ("tone_b_ms", "Tone B Duration"),
            ("hold_ms", "Relay Hold"),
            ("rearm_ms", "Re-arm Delay"),
            ("repeat_suppression_ms", "Repeat Suppression"),
        ]
        errors: list[str] = []
        for key, label in fields:
            try:
                value = self._parse_int_field(key, label)
                self._validate_ms_field(value, label)
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            ms_errors = self._collect_ms_field_errors()
            if ms_errors:
                raise ValueError("\n".join(ms_errors))

            tone_a_ms = self._parse_int_field("tone_a_ms", "Tone A Duration")
            tone_b_ms = self._parse_int_field("tone_b_ms", "Tone B Duration")
            hold_ms = self._parse_int_field("hold_ms", "Relay Hold")
            rearm_ms = self._parse_int_field("rearm_ms", "Re-arm Delay")
            repeat_ms = self._parse_int_field("repeat_suppression_ms", "Repeat Suppression")
            tone = TonePair(
                name=self.inputs["name"].value.strip() or "Tone",
                tone_a_hz=self._parse_float_field("tone_a_hz", "Tone A"),
                tone_b_hz=self._parse_float_field("tone_b_hz", "Tone B"),
                tone_a_ms=tone_a_ms,
                tone_b_ms=tone_b_ms,
                tolerance_pct=self._parse_float_field("tolerance_pct", "Frequency Tolerance"),
                min_snr_db=self._parse_float_field("min_snr_db", "Minimum SNR"),
                action=ToneAction(
                    gpio_pin=self._parse_int_field("gpio_pin", "GPIO Pin"),
                    active_high=self._parse_bool_field("active_high", "Relay Active High"),
                    hold_ms=hold_ms,
                    rearm_ms=rearm_ms,
                    repeat_suppression_ms=repeat_ms,
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
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


class DetectionRuntime:
    """Runs live detection in background while the TUI remains interactive."""

    def __init__(self, cfg: ServiceConfig, on_status, on_detect, on_level, on_debug):
        self.cfg = cfg.model_copy(deep=True)
        self.cfg.audio.device = resolve_input_device(self.cfg.audio.device)
        self.cfg.audio.sample_rate = resolve_sample_rate(
            self.cfg.audio.device,
            self.cfg.audio.sample_rate,
        )
        self.on_status = on_status
        self.on_detect = on_detect
        self.on_level = on_level
        self.on_debug = on_debug
        self.relay = RelayDriver()
        self.detector = DetectorEngine(self.cfg)
        self.audio_queue = queue.Queue(maxsize=50)
        self.audio = AudioStreamer(self.cfg.audio, self.cfg.frame_samples, self.audio_queue)
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.running = False
        self.last_debug_emit_ms = 0

    def start(self) -> None:
        if self.running:
            return
        self.stop_event.clear()
        self.audio.start()
        self.worker = threading.Thread(target=self._loop, name="qcii-detect", daemon=True)
        self.running = True
        self.worker.start()

    def stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.audio.stop()
        if self.worker:
            self.worker.join(timeout=2)
        self.running = False

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                block = self.audio_queue.get(timeout=0.3)
            except queue.Empty:
                audio_error = self.audio.health_error()
                if audio_error:
                    self.on_status(f"Audio error: {audio_error}")
                    self.stop_event.set()
                continue
            try:
                timestamp = int(time.time() * 1000)
                rms = float(np.sqrt(np.mean(np.square(block)))) if len(block) else 0.0
                peak = float(np.max(np.abs(block))) if len(block) else 0.0
                self.on_level(rms, peak)
                debug = self.detector.debug_block(block, timestamp)
                if timestamp - self.last_debug_emit_ms >= 1000:
                    self.last_debug_emit_ms = timestamp
                    self.on_debug(debug)
                events = self.detector.process_block(block, timestamp)
                for event in events:
                    self.relay.activate(event.pair.action)
                    self.on_detect(event.pair.name, event.timestamp_ms)
            except Exception as exc:
                self.on_status(f"Detection error: {exc}")


class QCIIConfigApp(App):
    CSS_PATH = None
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "save", "Save"),
        ("r", "reload", "Reload"),
        ("p", "pulse", "Test pulse"),
        ("d", "toggle_detect", "Start/Stop detect"),
    ]
    config_path: reactive[Path] = reactive(Path(DEFAULT_CONFIG_PATH))

    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.manager = ConfigManager(config_path)
        self.relay = RelayDriver()
        self.runtime: Optional[DetectionRuntime] = None
        self.delete_arm_row: Optional[int] = None
        self.delete_arm_timer = None
        self.auto_start_timer = None
        self.file_logger = logging.getLogger("qcii_detector.tui.runtime")
        self.file_logger.setLevel(logging.INFO)
        self.file_logger.propagate = False
        self.tail_timer = None
        self._configure_file_logging()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        with Horizontal():
            with Vertical(id="left"):
                yield Static(f"Config: {self.config_path}", id="path")
                self.tone_table = ToneTable(id="tones")
                self.tone_table.cursor_type = "row"
                self.tone_table.zebra_stripes = True
                self.tone_table.add_columns("Name", "Tone A", "Tone B", "GPIO", "Hold ms")
                self.refresh_tones()
                yield self.tone_table
                self.selected_station = Static("Selected Station: none")
                yield self.selected_station
                with Horizontal():
                    yield Button("Add", id="add")
                    yield Button("Edit", id="edit")
                    self.delete_button = Button("Delete", id="delete", variant="warning")
                    yield self.delete_button
            with Vertical(id="right"):
                yield Static("Audio settings", classes="section")
                self.audio_rate = Input(
                    value="" if self.manager.config.audio.sample_rate is None else str(self.manager.config.audio.sample_rate),
                    placeholder="Sample rate (blank = auto/device default)",
                )
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
                self.detect_state = Static("Detector: STOPPED")
                yield self.detect_state
                self.auto_start_state = Static(self._auto_start_status_text())
                yield self.auto_start_state
                self.auto_start_button = Button(
                    self._auto_start_button_label(),
                    id="toggle_auto_start",
                )
                yield self.auto_start_button
                self.vu_meter = Static("Input Level: [--------------------] -inf dBFS | peak 0.000")
                yield self.vu_meter
                yield Button("Start Detection", id="start_detect", variant="success")
                yield Button("Stop Detection", id="stop_detect", variant="error")
                yield Button("Test Pulse", id="pulse")
                yield Button("Save", id="save", variant="primary")
                yield Button("Reload from disk", id="reload")
                self.status = Log(highlight=False, max_lines=200)
                yield self.status
                yield Static("Persistent Log Tail", classes="section")
                yield Button("Refresh Log Tail", id="refresh_tail")
                self.log_tail = Log(highlight=False, max_lines=400)
                yield self.log_tail

    def refresh_tones(self):
        self.tone_table.clear()
        for idx, pair in enumerate(self.manager.config.tone_pairs):
            self.tone_table.add_pair(idx, pair)
        self._clear_delete_arm()
        self._update_selected_station()

    def action_save(self):
        self.save_config()

    def action_reload(self):
        self.reload_config()

    def action_pulse(self):
        self.test_pulse()

    def action_toggle_detect(self):
        if self.runtime and self.runtime.running:
            self.stop_detection()
        else:
            self.start_detection()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "add":
            self._clear_delete_arm()
            self.push_screen(ToneEditScreen(), self._add_tone_callback)
        elif button_id == "edit":
            self._clear_delete_arm()
            self.edit_selected()
        elif button_id == "delete":
            self.delete_selected()
        elif button_id == "save":
            self._clear_delete_arm()
            self.save_config()
        elif button_id == "reload":
            self._clear_delete_arm()
            self.reload_config()
        elif button_id == "pulse":
            self._clear_delete_arm()
            self.test_pulse()
        elif button_id == "start_detect":
            self._clear_delete_arm()
            self.start_detection()
        elif button_id == "stop_detect":
            self._clear_delete_arm()
            self.stop_detection()
        elif button_id == "refresh_tail":
            self._clear_delete_arm()
            self.refresh_log_tail()
        elif button_id == "toggle_auto_start":
            self._clear_delete_arm()
            self.toggle_auto_start()

    def _selected_index(self) -> Optional[int]:
        row = self.tone_table.cursor_row
        if row is None:
            return None
        if row < 0 or row >= len(self.manager.config.tone_pairs):
            return None
        return row

    def edit_selected(self):
        idx = self._selected_index()
        if idx is None:
            self.push_screen(MessageScreen("No tone selected"))
            return
        self._clear_delete_arm()
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
        if self.delete_arm_row != idx:
            self._arm_delete(idx)
            return
        del self.manager.config.tone_pairs[idx]
        self._log_status(f"Deleted tone pair: row {idx + 1}")
        self.refresh_tones()

    def reload_config(self):
        self.stop_detection()
        self.manager = ConfigManager(self.config_path)
        self._configure_file_logging()
        self.refresh_tones()
        self.audio_rate.value = str(self.manager.config.audio.sample_rate)
        if self.manager.config.audio.sample_rate is None:
            self.audio_rate.value = ""
        self.audio_frame.value = str(self.manager.config.audio.frame_ms)
        self.audio_device.value = "" if self.manager.config.audio.device is None else str(self.manager.config.audio.device)
        self.log_level.value = self.manager.config.logging.level
        self.log_file.value = self.manager.config.logging.file or ""
        self._update_auto_start_widgets()
        self._log_status("Reloaded from disk")
        self.refresh_log_tail()
        self._warn_non_writable_paths(show_popup=False)
        self._schedule_auto_start()

    def save_config(self):
        try:
            self._apply_form_to_config()
            self._warn_non_writable_paths(show_popup=False)
            err = self.manager.validate()
            if err:
                self.push_screen(MessageScreen(f"Validation error:\n{err}"))
                return
            self.manager.save()
            self._configure_file_logging()
            self._log_status(f"Saved to {self.config_path}")
            self.refresh_log_tail()
        except Exception as exc:
            self.push_screen(MessageScreen(f"Save failed: {exc}"))

    def _apply_form_to_config(self) -> None:
        cfg = self.manager.config
        cfg.audio.sample_rate = int(self.audio_rate.value) if self.audio_rate.value.strip() else None
        cfg.audio.frame_ms = int(self.audio_frame.value)
        cfg.audio.device = self.audio_device.value or None
        cfg.logging.level = self.log_level.value or "INFO"
        cfg.logging.file = self.log_file.value or None

    def start_detection(self):
        self._cancel_auto_start()
        if self.runtime and self.runtime.running:
            self._log_status("Detection already running")
            return
        try:
            self._apply_form_to_config()
            self._warn_non_writable_paths(show_popup=False)
            err = self.manager.validate()
            if err:
                self.push_screen(MessageScreen(f"Validation error:\n{err}"))
                return
        except Exception as exc:
            self.push_screen(MessageScreen(f"Cannot start detection: {exc}"))
            return

        cfg = self.manager.config.model_copy(deep=True)
        self.runtime = DetectionRuntime(
            cfg,
            on_status=lambda msg: self.call_from_thread(self._set_runtime_status, msg),
            on_detect=lambda name, ts: self.call_from_thread(self._on_detection, name, ts),
            on_level=lambda rms, peak: self.call_from_thread(self._update_vu_meter, rms, peak),
            on_debug=lambda debug: self.call_from_thread(self._on_detection_debug, debug),
        )
        try:
            self.runtime.start()
            self._set_detector_state(True)
            self._log_status("Detection started")
        except Exception as exc:
            self.push_screen(MessageScreen(f"Failed to start detection: {exc}"))
            self.runtime = None

    def stop_detection(self):
        self._cancel_auto_start()
        if self.runtime and self.runtime.running:
            self.runtime.stop()
            self._log_status("Detection stopped")
        self.runtime = None
        self._set_detector_state(False)
        self._update_vu_meter(0.0, 0.0)

    def on_mount(self) -> None:
        self.tail_timer = self.set_interval(2.0, self.refresh_log_tail)
        self.refresh_log_tail()
        self._warn_non_writable_paths(show_popup=True)
        self._update_auto_start_widgets()
        self._schedule_auto_start()

    def on_unmount(self, event: events.Unmount) -> None:
        self.stop_detection()
        self._clear_delete_arm()
        self._cancel_auto_start()
        if self.tail_timer is not None:
            self.tail_timer.stop()
        for handler in list(self.file_logger.handlers):
            self.file_logger.removeHandler(handler)
            handler.close()

    def on_data_table_cell_highlighted(self, event) -> None:
        self._clear_delete_arm()
        self._update_selected_station()

    def _set_detector_state(self, running: bool) -> None:
        self.detect_state.update("Detector: RUNNING" if running else "Detector: STOPPED")

    def _on_detection(self, pair_name: str, timestamp_ms: int) -> None:
        self._log_status(f"Detected {pair_name} at {timestamp_ms} ms")

    def _on_detection_debug(self, debug) -> None:
        self._log_status(
            "Detect debug: "
            f"peak {debug.peak_freq_hz:.1f} Hz, "
            f"SNR {debug.snr_db:.1f} dB, "
            f"nearest {debug.best_pair_name} "
            f"(delta {debug.best_pair_delta_hz:.1f} Hz)"
        )

    def _update_vu_meter(self, rms: float, peak: float) -> None:
        bar_width = 20
        rms_db = 20.0 * np.log10(max(rms, 1e-6))
        normalized = max(0.0, min(1.0, (rms_db + 60.0) / 60.0))
        filled = int(round(normalized * bar_width))
        bar = "#" * filled + "-" * (bar_width - filled)
        db_text = f"{rms_db:5.1f} dBFS" if rms > 0 else "-inf dBFS"
        style = self._vu_style(rms_db, peak)
        meter = Text("Input Level: ")
        meter.append("[", style="bold white")
        meter.append(bar, style=style)
        meter.append("]", style="bold white")
        meter.append(f" {db_text} | peak {peak:0.3f}", style="white")
        self.vu_meter.update(meter)

    def _vu_style(self, rms_db: float, peak: float) -> str:
        if peak >= 0.98 or rms_db >= -3.0:
            return "bold red"
        if peak >= 0.85 or rms_db >= -12.0:
            return "bold yellow"
        if rms_db >= -30.0:
            return "bold green"
        return "dim cyan"

    def _set_runtime_status(self, msg: str) -> None:
        self._log_status(msg)
        running = bool(self.runtime and self.runtime.running)
        self._set_detector_state(running)

    def _arm_delete(self, row: int) -> None:
        self._clear_delete_arm()
        self.delete_arm_row = row
        if hasattr(self, "delete_button"):
            self.delete_button.label = "Confirm Delete (3s)"
        self._log_status("Press Delete again within 3 seconds to remove the selected station")
        self.delete_arm_timer = self.set_timer(3.0, self._clear_delete_arm)

    def _clear_delete_arm(self) -> None:
        self.delete_arm_row = None
        if self.delete_arm_timer is not None:
            self.delete_arm_timer.stop()
            self.delete_arm_timer = None
        if hasattr(self, "delete_button"):
            self.delete_button.label = "Delete"

    def _update_selected_station(self) -> None:
        idx = self._selected_index()
        if not hasattr(self, "selected_station"):
            return
        if idx is None or idx >= len(self.manager.config.tone_pairs):
            self.selected_station.update("Selected Station: none")
            return
        pair = self.manager.config.tone_pairs[idx]
        self.selected_station.update(
            f"Selected Station: {pair.name} ({pair.tone_a_hz:.1f}/{pair.tone_b_hz:.1f} Hz)"
        )

    def toggle_auto_start(self) -> None:
        startup = self.manager.config.startup
        startup.auto_start_detection = not startup.auto_start_detection
        self._update_auto_start_widgets()
        if startup.auto_start_detection:
            self._log_status(
                f"Auto-start enabled; detection will start after {startup.startup_delay_sec} seconds on TUI launch"
            )
        else:
            self._log_status("Auto-start disabled")
            self._cancel_auto_start()

    def _auto_start_status_text(self) -> str:
        startup = self.manager.config.startup
        status = "ENABLED" if startup.auto_start_detection else "DISABLED"
        return f"Auto-start Detection: {status} ({startup.startup_delay_sec}s delay)"

    def _auto_start_button_label(self) -> str:
        return "Disable Auto-Start" if self.manager.config.startup.auto_start_detection else "Enable Auto-Start"

    def _update_auto_start_widgets(self) -> None:
        if hasattr(self, "auto_start_state"):
            self.auto_start_state.update(self._auto_start_status_text())
        if hasattr(self, "auto_start_button"):
            self.auto_start_button.label = self._auto_start_button_label()

    def _schedule_auto_start(self) -> None:
        self._cancel_auto_start()
        startup = self.manager.config.startup
        if not startup.auto_start_detection:
            return
        self._log_status(f"Auto-starting detection in {startup.startup_delay_sec} seconds")
        self.auto_start_timer = self.set_timer(startup.startup_delay_sec, self._auto_start_detection)

    def _auto_start_detection(self) -> None:
        self.auto_start_timer = None
        if self.manager.config.startup.auto_start_detection and not (self.runtime and self.runtime.running):
            self.start_detection()

    def _cancel_auto_start(self) -> None:
        if self.auto_start_timer is not None:
            self.auto_start_timer.stop()
            self.auto_start_timer = None

    def _configure_file_logging(self) -> None:
        for handler in list(self.file_logger.handlers):
            self.file_logger.removeHandler(handler)
            handler.close()
        log_path = self.manager.config.logging.file
        if not log_path:
            return
        try:
            path = Path(log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                str(path),
                maxBytes=self.manager.config.logging.max_bytes,
                backupCount=self.manager.config.logging.backup_count,
            )
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            self.file_logger.addHandler(handler)
        except Exception as exc:
            LOG.warning("Unable to configure file logging at %s: %s", log_path, exc)

    def _nearest_existing_parent(self, path: Path) -> Path:
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return parent

    def _is_writable_target(self, path: Path) -> bool:
        if path.exists():
            return os.access(path, os.W_OK)
        parent = self._nearest_existing_parent(path)
        return os.access(parent, os.W_OK | os.X_OK)

    def _warn_non_writable_paths(self, show_popup: bool) -> None:
        warnings: list[str] = []
        if not self._is_writable_target(self.config_path):
            warnings.append(f"Config path not writable by current user: {self.config_path}")

        log_path_value = self.manager.config.logging.file
        if log_path_value:
            log_path = Path(log_path_value)
            if not self._is_writable_target(log_path):
                warnings.append(f"Log path not writable by current user: {log_path}")

        for warning in warnings:
            self._log_status(f"WARNING: {warning}")

        if warnings and show_popup:
            self.push_screen(MessageScreen("\n".join(warnings)))

    def _read_log_tail_lines(self, limit: int = 80) -> list[str]:
        log_path = self.manager.config.logging.file
        if not log_path:
            return []
        path = Path(log_path)
        if not path.exists():
            return []
        max_bytes = 64 * 1024
        try:
            with open(path, "rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(size - max_bytes, 0))
                data = handle.read()
        except Exception:
            return []
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-limit:]

    def refresh_log_tail(self) -> None:
        if not hasattr(self, "log_tail"):
            return
        lines = self._read_log_tail_lines(limit=80)
        try:
            self.log_tail.clear()
        except Exception:
            pass
        if not lines:
            self.log_tail.write_line("No log file data available.")
            return
        for line in lines:
            self.log_tail.write_line(line)

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
        if self.file_logger.handlers:
            self.file_logger.info(msg)


def run_tui(config_path: str | Path):
    path = Path(config_path)
    if not path.exists():
        raise SystemExit(f"Config file not found: {path}")
    app = QCIIConfigApp(path)
    app.run()
