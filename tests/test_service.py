from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from airfare_monitor.config import (
    AppSettings,
    BrowserSettings,
    CollectionSettings,
    ExcelSettings,
    MailSettings,
    ScheduleSettings,
    StorageSettings,
)
from airfare_monitor.errors import CollectionError
from airfare_monitor.models import (
    EtdWindow,
    FlightSnapshot,
    LegConfig,
    LegResult,
    LegStatus,
    PreferredSchedule,
    RunReport,
    RunStatus,
)
from airfare_monitor.service import MonitorService


def route() -> LegConfig:
    return LegConfig("leg-1", True, "SHA", "KUL", date(2026, 9, 27), EtdWindow(time(0), time(23, 59)), True, Decimal("1000"), 10, 1, 0, "economy")


def settings(root: Path) -> AppSettings:
    return AppSettings(
        ScheduleSettings("Asia/Shanghai", 30, 0, True),
        BrowserSettings(False, root / "profile", 9333, 45, 30, 2, "https://example.test/{origin}/{destination}/{date}"),
        CollectionSettings("qunar", "CNY", True, 0),
        StorageSettings(root / "monitor.sqlite3", 7),
        ExcelSettings(root / "outputs", 24),
        MailSettings(False, "smtp.example.test", 465, "ssl", "U", "P", "S", "R", True, "[航价监控]", "[低价命中]"),
    )


class FakeBrowser:
    def __init__(self):
        self.calls = 0
        self.restarts = 0
        self.closed = False

    def collect(self, leg, now):
        self.calls += 1
        if self.calls == 1:
            raise CollectionError("temporary")
        return LegResult(leg, LegStatus.SUCCESS, now(), completed_response=True)

    def restart(self):
        self.restarts += 1

    def close(self):
        self.closed = True


class ServiceTests(unittest.TestCase):
    def test_failed_leg_is_retried_once(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            service = MonitorService(
                [route()],
                settings(Path(directory)),
                browser=browser,
                now=lambda: datetime(2026, 8, 31, 10),
            )
            report, workbook = service.run_once()
            self.assertEqual(browser.calls, 2)
            self.assertEqual(report.status, RunStatus.SUCCESS)
            self.assertTrue(Path(workbook).is_file())

    def test_first_preferred_price_uses_current_match_as_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            preference = PreferredSchedule("上海 → 吉隆坡", time(7, 25), time(13))
            configured = replace(route(), preferred_schedules=(preference,))
            captured = datetime(2026, 8, 31, 10)
            flight = FlightSnapshot(
                "signature",
                ("MU001",),
                ("MU",),
                "SHA",
                "KUL",
                configured.departure_date,
                datetime(2026, 9, 27, 7, 25),
                datetime(2026, 9, 27, 13),
                335,
                1,
                True,
                Decimal("800"),
                Decimal("260"),
                Decimal("1060"),
                "CNY",
                None,
                None,
                None,
                None,
                captured,
            )
            result = LegResult(
                configured,
                LegStatus.SUCCESS,
                captured,
                preferred_matches=[flight],
                completed_response=True,
            )
            service = MonitorService(
                [configured],
                settings(Path(directory)),
                browser=FakeBrowser(),
                now=lambda: captured,
            )
            service.store.initialize()
            service._attach_preferred_price_references([result], before=captured)
            reference = result.preferred_price_references[0]
            self.assertEqual(reference.first_total_price_cny, Decimal("1060"))
            self.assertEqual(reference.first_captured_at, captured)
            self.assertIsNone(reference.previous_total_price_cny)

    def test_candidate_price_references_use_existing_snapshot_history(self):
        with tempfile.TemporaryDirectory() as directory:
            first_at = datetime(2026, 8, 31, 9)
            previous_at = datetime(2026, 8, 31, 9, 30)
            current_at = datetime(2026, 8, 31, 10)
            flight = FlightSnapshot(
                "stable-signature",
                ("MU001",),
                ("MU",),
                "SHA",
                "KUL",
                route().departure_date,
                datetime(2026, 9, 27, 7, 25),
                datetime(2026, 9, 27, 13),
                335,
                1,
                True,
                Decimal("800"),
                Decimal("260"),
                Decimal("1060"),
                "CNY",
                None,
                None,
                None,
                None,
                first_at,
            )
            service = MonitorService(
                [route()],
                settings(Path(directory)),
                browser=FakeBrowser(),
                now=lambda: current_at,
            )
            service.store.initialize()
            first_result = LegResult(route(), LegStatus.SUCCESS, first_at, flights=[flight], completed_response=True)
            service.store.save_report(
                RunReport("history-1", first_at, first_at, RunStatus.SUCCESS, [first_result], set())
            )
            previous_flight = replace(flight, total_price_cny=Decimal("1030"), captured_at=previous_at)
            previous_result = LegResult(
                route(), LegStatus.SUCCESS, previous_at, flights=[previous_flight], completed_response=True
            )
            service.store.save_report(
                RunReport(
                    "history-2", previous_at, previous_at, RunStatus.SUCCESS, [previous_result], set()
                )
            )
            current_flight = replace(flight, total_price_cny=Decimal("990"), captured_at=current_at)
            current_result = LegResult(
                route(), LegStatus.SUCCESS, current_at, flights=[current_flight], completed_response=True
            )
            service._attach_flight_price_references([current_result], before=current_at)
            reference = current_result.flight_price_references["stable-signature"]
            self.assertEqual(reference.first_total_price_cny, Decimal("1060"))
            self.assertEqual(reference.first_captured_at, first_at)
            self.assertEqual(reference.previous_total_price_cny, Decimal("1030"))
            self.assertEqual(reference.previous_captured_at, previous_at)
