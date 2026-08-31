from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from airfare_monitor.config import MailSettings
from airfare_monitor.excel_report import generate_workbook
from airfare_monitor.mail import build_message, build_subject
from airfare_monitor.models import EtdWindow, LegConfig, LegResult, LegStatus, RunReport, RunStatus
from airfare_monitor.storage import SQLiteStore


def route() -> LegConfig:
    return LegConfig("leg-1", True, "SHA", "KUL", date(2026, 9, 27), EtdWindow(time(0), time(23, 59)), True, Decimal("1000"), 10, 1, 0, "economy", "上海", "吉隆坡")


def report(status: RunStatus = RunStatus.SUCCESS) -> RunReport:
    captured = datetime(2026, 8, 31, 9, 30)
    result = LegResult(route(), LegStatus.SUCCESS, captured, completed_response=True)
    return RunReport("run-1", captured, captured, status, [result], set())


MAIL = MailSettings(False, "smtp.example.com", 465, "ssl", "U", "P", "S", "R", True, "[航价监控]", "[低价命中]")


class ReportTests(unittest.TestCase):
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
