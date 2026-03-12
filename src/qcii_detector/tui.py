from __future__ import annotations

import logging
import os
import queue
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import yaml
from pydantic import ValidationError
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Log, Static

from .audio import AudioStreamer
from .config import DEFAULT_CONFIG_PATH, ServiceConfig, ToneAction, TonePair, load_config
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

    def __init__(self, cfg: ServiceConfig, on_status, on_detect):
        self.cfg = cfg
        self.on_status = on_status
        self.on_detect = on_detect
        self.relay = RelayDriver()
        self.detector = DetectorEngine(cfg)
        self.audio_queue = queue.Queue(maxsize=50)
        self.audio = AudioStreamer(cfg.audio, cfg.frame_samples, self.audio_queue)
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.running = False

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
                continue
            try:
                timestamp = int(time.time() * 1000)
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
                self.detect_state = Static("Detector: STOPPED")
                yield self.detect_state
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
            self.push_screen(ToneEditScreen(), self._add_tone_callback)
        elif button_id == "edit":
            self.edit_selected()
        elif button_id == "delete":
            self.delete_selected()
        elif button_id == "save":
            self.save_config()
        elif button_id == "reload":
            self.reload_config()
        elif button_id == "pulse":
            self.test_pulse()
        elif button_id == "start_detect":
            self.start_detection()
        elif button_id == "stop_detect":
            self.stop_detection()
        elif button_id == "refresh_tail":
            self.refresh_log_tail()

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
        self.stop_detection()
        self.manager = ConfigManager(self.config_path)
        self._configure_file_logging()
        self.refresh_tones()
        self.audio_rate.value = str(self.manager.config.audio.sample_rate)
        self.audio_frame.value = str(self.manager.config.audio.frame_ms)
        self.audio_device.value = "" if self.manager.config.audio.device is None else str(self.manager.config.audio.device)
        self.log_level.value = self.manager.config.logging.level
        self.log_file.value = self.manager.config.logging.file or ""
        self._log_status("Reloaded from disk")
        self.refresh_log_tail()
        self._warn_non_writable_paths(show_popup=False)

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
        cfg.audio.sample_rate = int(self.audio_rate.value)
        cfg.audio.frame_ms = int(self.audio_frame.value)
        cfg.audio.device = self.audio_device.value or None
        cfg.logging.level = self.log_level.value or "INFO"
        cfg.logging.file = self.log_file.value or None

    def start_detection(self):
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
        )
        try:
            self.runtime.start()
            self._set_detector_state(True)
            self._log_status("Detection started")
        except Exception as exc:
            self.push_screen(MessageScreen(f"Failed to start detection: {exc}"))
            self.runtime = None

    def stop_detection(self):
        if self.runtime and self.runtime.running:
            self.runtime.stop()
            self._log_status("Detection stopped")
        self.runtime = None
        self._set_detector_state(False)

    def on_mount(self) -> None:
        self.tail_timer = self.set_interval(2.0, self.refresh_log_tail)
        self.refresh_log_tail()
        self._warn_non_writable_paths(show_popup=True)

    def on_unmount(self, event: events.Unmount) -> None:
        self.stop_detection()
        if self.tail_timer is not None:
            self.tail_timer.stop()
        for handler in list(self.file_logger.handlers):
            self.file_logger.removeHandler(handler)
            handler.close()

    def _set_detector_state(self, running: bool) -> None:
        self.detect_state.update("Detector: RUNNING" if running else "Detector: STOPPED")

    def _on_detection(self, pair_name: str, timestamp_ms: int) -> None:
        self._log_status(f"Detected {pair_name} at {timestamp_ms} ms")

    def _set_runtime_status(self, msg: str) -> None:
        self._log_status(msg)
        running = bool(self.runtime and self.runtime.running)
        self._set_detector_state(running)

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
