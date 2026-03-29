from __future__ import annotations

import getpass
import grp
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


UNIT_NAME = "qcii.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME
LAUNCHER_RELATIVE_PATH = Path("scripts/run-qcii-service.sh")


@dataclass
class BootServiceStatus:
    installed: bool
    enabled: bool
    active: bool

    def label(self) -> str:
        if self.enabled:
            state = "ENABLED"
        elif self.installed:
            state = "DISABLED"
        else:
            state = "NOT INSTALLED"
        if self.active:
            state = f"{state} (running)"
        return f"Headless Service On Boot: {state}"


class SystemdServiceManager:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).resolve()

    def service_status(self) -> BootServiceStatus:
        installed = UNIT_PATH.exists()
        enabled = self._systemctl_read("is-enabled", UNIT_NAME) == "enabled"
        active = self._systemctl_read("is-active", UNIT_NAME) == "active"
        return BootServiceStatus(installed=installed, enabled=enabled, active=active)

    def render_unit(self) -> str:
        repo_root = self._repo_root()
        launcher = repo_root / LAUNCHER_RELATIVE_PATH
        user = getpass.getuser()
        group = grp.getgrgid(os.getgid()).gr_name
        return (
            "[Unit]\n"
            "Description=QCII Two-Tone Detector\n"
            "After=sound.target network.target\n"
            "Wants=sound.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={user}\n"
            f"Group={group}\n"
            "SupplementaryGroups=audio gpio\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            "Environment=GPIOZERO_PIN_FACTORY=lgpio\n"
            f"WorkingDirectory={repo_root}\n"
            f"ExecStart={launcher} {self.config_path}\n"
            "Restart=on-failure\n"
            "RestartSec=3\n"
            "\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )

    def enable_on_boot(self) -> tuple[bool, str]:
        ok, message = self._write_unit_file(self.render_unit())
        if not ok:
            return False, message

        ok, message = self._systemctl_write("daemon-reload")
        if not ok:
            return False, message

        ok, message = self._systemctl_write("enable", UNIT_NAME)
        if not ok:
            return False, message

        return True, "Headless service enabled on boot (starts through the venv launcher)"

    def disable_on_boot(self) -> tuple[bool, str]:
        ok, message = self._systemctl_write("disable", UNIT_NAME)
        if not ok:
            return False, message
        return True, "Headless service disabled on boot"

    def _repo_root(self) -> Path:
        python_bin = Path(sys.executable).resolve()
        return python_bin.parents[2]

    def _systemctl_read(self, *args: str) -> str:
        result = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _systemctl_write(self, *args: str) -> tuple[bool, str]:
        command = ["systemctl", *args]
        if os.geteuid() != 0:
            command = ["sudo", "-n", *command]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, result.stdout.strip() or "ok"
        return False, _format_subprocess_error(result)

    def _write_unit_file(self, content: str) -> tuple[bool, str]:
        if os.geteuid() == 0:
            try:
                UNIT_PATH.write_text(content, encoding="utf-8")
            except Exception as exc:
                return False, str(exc)
            return True, str(UNIT_PATH)

        result = subprocess.run(
            ["sudo", "-n", "tee", str(UNIT_PATH)],
            input=content,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, str(UNIT_PATH)
        return False, _format_subprocess_error(result)


def _format_subprocess_error(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout).strip()
    if "password is required" in text.lower():
        return (
            "This action needs sudo without an interactive password prompt. "
            "Run the equivalent systemctl command manually, or launch the TUI from a shell with sudo access."
        )
    return text or "command failed"
