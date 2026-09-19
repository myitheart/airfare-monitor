from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class MacAutostartTests(unittest.TestCase):
    """macOS LaunchAgent 注册逻辑；launchctl 全部打桩，不真正加载。"""

    def setUp(self):
        from airfare_monitor.desktop_app import autostart

        self.module = autostart
        # 无论在哪个平台运行，都强制走 macOS LaunchAgent 分支：
        # Windows 上 Path.home() 跟随 USERPROFILE 而非 HOME，两个都指到临时目录。
        self._mac_patcher = mock.patch.object(autostart, "_is_mac", return_value=True)
        self._mac_patcher.start()
        self._home = os.environ.get("HOME")
        self._user_profile = os.environ.get("USERPROFILE")
        self._temporary = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._temporary.name
        os.environ["USERPROFILE"] = self._temporary.name
        self.manager = autostart.AutostartManager(command='"/opt/app binary" --background')

    def tearDown(self):
        self._mac_patcher.stop()
        for name, original in (("HOME", self._home), ("USERPROFILE", self._user_profile)):
            if original is not None:
                os.environ[name] = original
            else:
                os.environ.pop(name, None)
        self._temporary.cleanup()

    def _plist(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{self.module.LAUNCH_AGENT_LABEL}.plist"

    def test_enable_writes_plist_and_loads_agent(self):
        launch_calls: list[list[str]] = []

        def fake_launchctl(*args: str) -> subprocess.CompletedProcess:
            launch_calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch.object(self.module, "_launchctl", side_effect=fake_launchctl):
            self.manager.set_enabled(True)
        plist = self._plist()
        self.assertTrue(plist.is_file())
        content = plist.read_text(encoding="utf-8")
        self.assertIn("ProgramArguments", content)
        self.assertIn("<string>/opt/app binary</string>", content)  # 路径含空格按独立 argv 落盘
        self.assertIn("--background", content)
        self.assertTrue(self.manager.is_enabled())
        self.assertTrue(any(call and call[0] == "bootstrap" for call in launch_calls))

    def test_disable_removes_plist(self):
        with mock.patch.object(self.module, "_launchctl", return_value=subprocess.CompletedProcess((), 0, "", "")):
            self.manager.set_enabled(True)
            self.assertTrue(self._plist().is_file())
            self.manager.set_enabled(False)
        self.assertFalse(self._plist().exists())
        self.assertFalse(self.manager.is_enabled())

    def test_enable_failure_removes_plist(self):
        failure = subprocess.CompletedProcess((), 1, "", "bootstrap failed")
        with mock.patch.object(self.module, "_launchctl", return_value=failure):
            with self.assertRaises(self.module.AutostartError):
                self.manager.set_enabled(True)
        self.assertFalse(self._plist().exists())

    def test_is_enabled_false_when_plist_missing(self):
        self.assertFalse(self.manager.is_enabled())

    def test_is_enabled_false_when_arguments_differ(self):
        with mock.patch.object(self.module, "_launchctl", return_value=subprocess.CompletedProcess((), 0, "", "")):
            self.manager.set_enabled(True)
        plist = self._plist()
        content = plist.read_text(encoding="utf-8").replace("--background", "--other")
        plist.write_text(content, encoding="utf-8")
        self.assertFalse(self.manager.is_enabled())


if __name__ == "__main__":
    unittest.main()
