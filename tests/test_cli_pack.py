"""CLI 四件套（price/history/routes/status/doctor）与浏览器强杀降级的测试。"""

from __future__ import annotations

import shutil
import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from airfare_monitor.cli import main as cli_main
from airfare_monitor.collector import _graceful_chromium_cls
from airfare_monitor.config import LegConfig, load_settings
from airfare_monitor.models import (
    EtdWindow,
    FlightSnapshot,
    LegResult,
    LegStatus,
    RunReport,
    RunStatus,
)
from airfare_monitor.storage import SQLiteStore

_EXAMPLE_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.example.yaml"

_ROUTES_YAML = """
legs:
  - id: demo-leg
    enabled: true
    origin_name_zh: 上海
    origin_airport_iata: SHA
    origin_airports: [PVG, SHA]
    destination_name_zh: 东京
    destination_airport_iata: TYO
    destination_airports: [NRT, HND]
    departure_date: '2026-11-26'
    etd_window: {start: '00:00', end: '23:59'}
    direct_only: true
    expected_total_price_cny: 1500
    top_n: 5
    adult_count: 2
    child_count: 0
    cabin_class: economy
"""


def _seed_store(settings) -> None:
    leg = LegConfig(
        id="demo-leg",
        enabled=True,
        origin_airport_iata="SHA",
        destination_airport_iata="TYO",
        departure_date=date(2026, 11, 26),
        etd_window=EtdWindow(time(0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=Decimal("1500"),
        top_n=5,
        adult_count=2,
        child_count=0,
        cabin_class="economy",
    )
    flight = FlightSnapshot(
        "sig-1", ("MU523",), ("MU",), "PVG", "NRT", date(2026, 11, 26),
        datetime(2026, 11, 26, 9, 10), datetime(2026, 11, 26, 12, 50), 160, 1, True,
        Decimal("1470"), Decimal("1060"), Decimal("2530"), "CNY", "9", 0, "0",
        "qunar.com", datetime.now() - timedelta(hours=2),
    )
    captured = datetime.now() - timedelta(hours=2)
    result = LegResult(leg, LegStatus.SUCCESS, captured, flights=[flight], completed_response=True)
    store = SQLiteStore(settings.storage.sqlite_path)
    store.initialize()
    store.save_report(RunReport("seed-1", captured, captured, RunStatus.SUCCESS, [result], set()))


class _Env:
    def __init__(self, seed: bool = True):
        self._temporary = TemporaryDirectory()
        root = Path(self._temporary.name)
        (root / "config").mkdir()
        shutil.copy2(_EXAMPLE_SETTINGS, root / "config" / "settings.yaml")
        (root / "config" / "routes.yaml").write_text(_ROUTES_YAML, encoding="utf-8")
        self.settings = load_settings(root / "config" / "settings.yaml")
        if seed:
            _seed_store(self.settings)

    @property
    def user_root(self) -> str:
        return str(Path(self._temporary.name))

    def close(self) -> None:
        self._temporary.cleanup()


class PriceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.env = _Env()

    def tearDown(self):
        self.env.close()

    def _run(self, *args: str) -> int:
        return cli_main(["--user-root", self.env.user_root, *args])

    def test_price_shows_latest(self):
        with mock.patch("builtins.print") as printer:
            code = self._run("price")
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("demo-leg", text)
        self.assertIn("¥2,530", text)

    def test_price_unknown_leg(self):
        self.assertEqual(self._run("price", "--leg", "nope"), 1)

    def test_history_within_window(self):
        with mock.patch("builtins.print") as printer:
            code = self._run("history", "--hours", "999999")
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("¥2,530", text)

    def test_history_empty_window(self):
        with mock.patch("builtins.print") as printer:
            code = self._run("history", "--hours", "1")
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("没有记录", text)


class RoutesCommandTests(unittest.TestCase):
    def setUp(self):
        self.env = _Env()

    def tearDown(self):
        self.env.close()

    def _run(self, *args: str) -> int:
        return cli_main(["--user-root", self.env.user_root, "routes", *args])

    def test_list_shows_route(self):
        with mock.patch("builtins.print") as printer:
            code = self._run("list")
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("demo-leg", text)
        self.assertIn("启用", text)

    def test_disable_then_enable_roundtrip(self):
        self.assertEqual(self._run("disable", "demo-leg"), 0)
        from airfare_monitor.config import load_routes

        legs = load_routes(Path(self.env.user_root) / "config" / "routes.yaml", allow_empty=True)
        self.assertFalse(legs[0].enabled)
        self.assertEqual(self._run("enable", "demo-leg"), 0)
        legs = load_routes(Path(self.env.user_root) / "config" / "routes.yaml", allow_empty=True)
        self.assertTrue(legs[0].enabled)

    def test_disable_unknown_leg(self):
        self.assertEqual(self._run("disable", "nope"), 1)


class StatusDoctorTests(unittest.TestCase):
    def setUp(self):
        self.env = _Env()

    def tearDown(self):
        self.env.close()

    def _run(self, *args: str) -> int:
        return cli_main(["--user-root", self.env.user_root, *args])

    def test_status_reports_lock_and_latest(self):
        with mock.patch("builtins.print") as printer:
            code = self._run("status")
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("调度锁", text)
        self.assertIn("demo-leg", text)
        self.assertIn("¥2,530", text)

    def test_doctor_passes_config_and_db(self):
        with mock.patch("builtins.print") as printer:
            code = self._run("doctor")
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertEqual(code, 0)
        self.assertIn("[PASS] routes.yaml 解析", text)
        self.assertIn("[PASS] settings.yaml 解析", text)
        self.assertIn("[PASS] 价格数据库", text)
        self.assertIn("签名身份", text)  # 源码运行为 SKIP 行


class GracefulChromiumTests(unittest.TestCase):
    def test_force_quit_downgrades_to_graceful(self):
        calls: list[tuple[int, bool]] = []

        class FakeChromium:
            def quit(self, timeout=5, force=False, del_data=False):
                calls.append((timeout, bool(force)))
                return "closed"

        graceful = _graceful_chromium_cls(FakeChromium)
        instance = graceful()
        self.assertEqual(instance.quit(3, True), "closed")
        self.assertEqual(calls, [(3, False)], "force=True 必须被降级为优雅关闭")
        self.assertIs(_graceful_chromium_cls(FakeChromium), graceful, "子类应按基类缓存")

    def test_normal_quit_passthrough(self):
        calls: list[tuple[int, bool]] = []

        class FakeChromium:
            def quit(self, timeout=5, force=False, del_data=False):
                calls.append((timeout, bool(force)))

        graceful = _graceful_chromium_cls(FakeChromium)
        graceful().quit(5, False)
        self.assertEqual(calls, [(5, False)])


if __name__ == "__main__":
    unittest.main()
