from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from airfare_monitor.config import MailSettings
from airfare_monitor.excel_report import generate_workbook
from airfare_monitor.mail import build_message, build_subject
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
from airfare_monitor.storage import SQLiteStore


def route() -> LegConfig:
    return LegConfig("leg-1", True, "SHA", "KUL", date(2026, 9, 27), EtdWindow(time(0), time(23, 59)), True, Decimal("1000"), 10, 1, 0, "economy", "上海", "吉隆坡")


def report(status: RunStatus = RunStatus.SUCCESS) -> RunReport:
    captured = datetime(2026, 8, 31, 9, 30)
    result = LegResult(route(), LegStatus.SUCCESS, captured, completed_response=True)
    return RunReport("run-1", captured, captured, status, [result], set())


MAIL = MailSettings(False, "smtp.example.com", 465, "ssl", "U", "P", "S", "R", True, "[航价监控]", "[低价命中]")


class ReportTests(unittest.TestCase):
    def test_subject_uses_enabled_leg_count(self):
        self.assertIn("1程更新", build_subject(report(), MAIL))

    def test_subject_marks_partial(self):
        self.assertIn("[部分失败]", build_subject(report(RunStatus.PARTIAL), MAIL))

    def test_message_has_plain_html_and_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            attachment = Path(directory) / "report.xlsx"
            attachment.write_bytes(b"test")
            message = build_message(report(), MAIL, sender="sender@example.com", recipients=["to@example.com"], attachment=attachment)
            self.assertTrue(message.is_multipart())
            self.assertEqual(len(list(message.iter_attachments())), 1)
            self.assertIn("上海（SHA） → 吉隆坡（KUL）", message.get_body(preferencelist=("plain",)).get_content())

    def test_message_includes_preferred_schedule_live_price(self):
        preference = PreferredSchedule(
            "上海浦东 → 吉隆坡",
            time(7, 25),
            time(13),
            origin_airport_iata="PVG",
            destination_airport_iata="KUL",
        )
        configured = replace(route(), preferred_schedules=(preference,))
        captured = datetime(2026, 8, 31, 9, 30)
        flight = FlightSnapshot(
            flight_signature="preferred-1",
            flight_codes=("MU001",),
            carrier_codes=("MU",),
            origin_airport_iata="PVG",
            destination_airport_iata="KUL",
            departure_date=date(2026, 9, 27),
            etd_local=datetime(2026, 9, 27, 7, 25),
            eta_local=datetime(2026, 9, 27, 13),
            duration_minutes=335,
            segment_count=1,
            is_direct=True,
            base_price_cny=Decimal("800"),
            tax_cny=Decimal("260"),
            total_price_cny=Decimal("1060"),
            currency_code="CNY",
            remaining_seats=None,
            free_baggage_piece=None,
            free_baggage_weight=None,
            source_domain=None,
            captured_at=captured,
        )
        result = LegResult(
            configured,
            LegStatus.SUCCESS,
            captured,
            flights=[flight],
            preferred_matches=[flight],
            completed_response=True,
        )
        value = RunReport("run-1", captured, captured, RunStatus.SUCCESS, [result], set())
        message = build_message(value, MAIL, sender="sender@example.com", recipients=["to@example.com"])
        body = message.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("关注时段（实时含税价）", body)
        self.assertIn("上海浦东 → 吉隆坡", body)
        self.assertIn("MU001 · ¥1,060", body)

    def test_sqlite_and_workbook_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            value = report()
            store.save_report(value)
            history = store.history(since=datetime(2026, 8, 30))
            self.assertEqual(len(history), 1)
            output = generate_workbook(value, history, Path(directory) / "outputs")
            workbook = load_workbook(output, read_only=True)
            try:
                self.assertEqual(workbook.sheetnames, ["本次汇总", "航程1", "24小时历史"])
            finally:
                workbook.close()
