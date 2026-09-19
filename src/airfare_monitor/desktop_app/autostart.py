"""Per-user autostart registration for the packaged desktop app.

Windows uses the current-user ``Run`` registry key; macOS registers a
LaunchAgent under ``~/Library/LaunchAgents`` and loads it with launchctl.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import plistlib
from pathlib import Path


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AirfareMonitor"
LAUNCH_AGENT_LABEL = "com.myitheart.airfare-monitor.desktop"


class AutostartError(RuntimeError):
    pass


def _is_mac() -> bool:
    return sys.platform == "darwin"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


class AutostartManager:
    def __init__(self, command: str | None = None):
        self.command = command or desktop_start_command()

    # ---- 查询 -----------------------------------------------------

    def is_enabled(self) -> bool:
        if _is_mac():
            return self._mac_is_enabled()
        if os.name != "nt":
            return False
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                return str(value) == self.command
        except OSError:
            return False

    def set_enabled(self, enabled: bool) -> None:
        if _is_mac():
            self._mac_set_enabled(enabled)
            return
        if os.name != "nt":
            if enabled:
                raise AutostartError("开机启动仅支持 Windows 与 macOS")
            return
        self._windows_set_enabled(enabled)

    # ---- Windows -------------------------------------------------

    def _windows_set_enabled(self, enabled: bool) -> None:
        try:
            import winreg

            if enabled:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                    winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, self.command)
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise AutostartError("无法更新当前用户的开机启动设置") from exc

    # ---- macOS ---------------------------------------------------

    def _plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

    def _command_arguments(self) -> list[str]:
        return shlex.split(self.command)

    def _mac_is_enabled(self) -> bool:
        plist = self._plist_path()
        if not plist.is_file():
            return False
        try:
            with plist.open("rb") as stream:
                content = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException):
            return False
        arguments = content.get("ProgramArguments")
        return isinstance(arguments, list) and arguments == self._command_arguments()

    def _mac_set_enabled(self, enabled: bool) -> None:
        plist = self._plist_path()
        if not enabled:
            # 未加载时 bootout 返回非零，属于正常情况；文件删除照常执行。
            _launchctl("bootout", f"gui/{os.getuid()}", str(plist))
            plist.unlink(missing_ok=True)
            return
        arguments = self._command_arguments()
        if not arguments:
            raise AutostartError("开机启动命令无效")
        plist.parent.mkdir(parents=True, exist_ok=True)
        argument_lines = "\n".join(f"        <string>{_xml_escape(item)}</string>" for item in arguments)
        plist.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
{argument_lines}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
""",
            encoding="utf-8",
        )
        _launchctl("bootout", f"gui/{os.getuid()}", str(plist))
        result = _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))
        if result.returncode != 0:
            plist.unlink(missing_ok=True)
            raise AutostartError(f"launchd 注册失败：{result.stderr.strip()[:120]}")


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def desktop_start_command() -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        args = [str(executable), "--background"]
        return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    if os.name == "nt":
        pythonw = executable.with_name("pythonw.exe")
        if not pythonw.is_file():
            pythonw = executable
        return subprocess.list2cmdline([str(pythonw), "-m", "airfare_monitor.desktop", "--background"])
    return shlex.join([str(executable), "-m", "airfare_monitor.desktop", "--background"])
