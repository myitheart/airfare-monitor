"""CLI daemon 热更新与桌面邮件通道对齐的回归测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from airfare_monitor import scheduler
from airfare_monitor.cli import _attach_desktop_mail


class _StopLoop(Exception):
    pass


class _StubService:
    """仅实现 run_forever 依赖的表面：settings.schedule、run_once、close。"""

    def __init__(self, interval_minutes: int, jitter_seconds: float = 0.0):
        self.settings = SimpleNamespace(
            schedule=SimpleNamespace(interval_minutes=interval_minutes, jitter_seconds=jitter_seconds)
        )
        self.closed = False

    def run_once(self, *, send_email: bool):
        assert send_email is True
        return SimpleNamespace(run_id="r", status="ok"), "workbook.xlsx"

    def close(self):
        self.closed = True


class DaemonHotReloadTests(unittest.TestCase):
    def test_each_cycle_rebuilds_service_from_current_config(self):
        built: list[_StubService] = []
        intervals = [1, 5]  # 模拟运行期间 settings.yaml 的间隔被修改
        factory = lambda: built.append(_StubService(intervals[len(built)])) or built[-1]

        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise _StopLoop()

        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "airfare-monitor.lock"
            # 单调时钟序列：第 1 轮 start=0/elapsed=10，第 2 轮 start=10/elapsed=190。
            with mock.patch.object(scheduler.time, "monotonic", side_effect=[0, 10, 10, 200]):
                with mock.patch.object(scheduler.time, "sleep", fake_sleep):
                    with self.assertRaises(_StopLoop):
                        scheduler.run_forever(factory, lock)

        self.assertEqual(len(built), 2, "每轮都应重建 service")
        self.assertTrue(all(service.closed for service in built), "每轮结束后都应释放采集资源")
        # 第 1 轮按旧间隔 1 分钟等待，第 2 轮按新间隔 5 分钟等待（热更新生效）。
        self.assertAlmostEqual(sleeps[0], max(0.0, 60 - 10))
        self.assertAlmostEqual(sleeps[1], max(0.0, 300 - 190))

    def test_config_error_does_not_kill_daemon(self):
        calls = {"count": 0}
        built: list[_StubService] = []

        def factory() -> _StubService:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("routes.yaml 解析失败")
            service = _StubService(1)
            built.append(service)
            return service

        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise _StopLoop()

        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "airfare-monitor.lock"
            with mock.patch.object(scheduler.time, "monotonic", return_value=0):
                with mock.patch.object(scheduler.time, "sleep", fake_sleep):
                    with self.assertRaises(_StopLoop):
                        scheduler.run_forever(factory, lock)
        self.assertEqual(calls["count"], 2, "配置读取失败不应终止 daemon")
        self.assertEqual(len(built), 1, "第二轮应按修复后的配置继续执行")
        self.assertTrue(built[0].closed)


class DesktopMailChannelTests(unittest.TestCase):
    def _settings_file(self, directory: Path, *, enabled: bool) -> Path:
        config = directory / "config"
        config.mkdir(parents=True, exist_ok=True)
        settings = config / "settings.yaml"
        settings.write_text(
            "\n".join(
                [
                    "schedule: {interval_minutes: 30, jitter_seconds: 0}",
                    "browser: {executable_path: '', headless: true}",
                    "collection: {top_n: 5}",
                    "storage: {sqlite_path: data/db.sqlite3, keep_raw_response_days: 7}",
                    "excel: {output_directory: outputs, history_hours: 24}",
                    "mail: {enabled: false}",
                    "desktop_mail:",
                    f"  enabled: {str(enabled).lower()}",
                    "  smtp_host: smtp.example.com",
                    "  smtp_port: 465",
                    "  security: ssl",
                    "  username: watcher@example.com",
                    "  sender: watcher@example.com",
                    "  recipients: [owner@example.com]",
                    "  attach_excel: true",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return settings

    def test_enabled_profile_attaches_credentials_delivery(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings_path = self._settings_file(Path(temporary), enabled=True)
            service = SimpleNamespace(settings=None, mail_delivery=None)
            with mock.patch(
                "airfare_monitor.desktop_app.credential_store.CredentialStore.get_secret",
                return_value="secret-code",
            ):
                _attach_desktop_mail(service, settings_path)
            self.assertIsNotNone(service.mail_delivery, "desktop_mail 启用时应挂接凭据直发通道")

    def test_disabled_profile_keeps_env_channel(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings_path = self._settings_file(Path(temporary), enabled=False)
            service = SimpleNamespace(settings=None, mail_delivery=None)
            _attach_desktop_mail(service, settings_path)
            self.assertIsNone(service.mail_delivery, "未启用时保持环境变量通道")

    def test_broken_profile_falls_back_without_raising(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings_path = Path(temporary) / "config" / "settings.yaml"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text("desktop_mail: 42\n", encoding="utf-8")
            service = SimpleNamespace(settings=None, mail_delivery=None)
            _attach_desktop_mail(service, settings_path)
            self.assertIsNone(service.mail_delivery)


if __name__ == "__main__":
    unittest.main()
