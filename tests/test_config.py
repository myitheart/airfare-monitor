from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from airfare_monitor.config import load_local_env, load_routes
from airfare_monitor.errors import ConfigError


VALID = """
legs:
  - id: leg-1
    enabled: true
    origin_airport_iata: SHA
    origin_name_zh: 上海
    destination_airport_iata: KUL
    destination_name_zh: 吉隆坡
    departure_date: '2026-09-27'
    etd_window: {start: '08:00', end: '18:00'}
    direct_only: true
    expected_total_price_cny: 1000
    top_n: 10
    adult_count: 1
    child_count: 0
    cabin_class: economy
"""


class ConfigTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.yaml"
            path.write_text(text, encoding="utf-8")
            return load_routes(path)

    def test_loads_valid_route_and_normalizes_iata(self):
        legs = self._load(VALID.replace("SHA", "sha"))
        self.assertEqual(legs[0].origin_airport_iata, "SHA")
        self.assertEqual(legs[0].etd_window.display(), "08:00-18:00")
        self.assertEqual(legs[0].route_display, "上海（SHA） → 吉隆坡（KUL）")

    def test_rejects_date_in_etd_window(self):
        with self.assertRaisesRegex(ConfigError, "HH:MM"):
            self._load(VALID.replace("start: '08:00'", "start: '2026-09-27'"))

    def test_rejects_airline_code_as_airport(self):
        with self.assertRaisesRegex(ConfigError, "三个英文字母"):
            self._load(VALID.replace("SHA", "MU"))

    def test_loads_git_ignored_env_without_overriding_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("AIRFARE_TEST_VALUE=file\n", encoding="utf-8")
            os.environ["AIRFARE_TEST_VALUE"] = "process"
            try:
                load_local_env(path)
                self.assertEqual(os.environ["AIRFARE_TEST_VALUE"], "process")
                load_local_env(path, override=True)
                self.assertEqual(os.environ["AIRFARE_TEST_VALUE"], "file")
            finally:
                os.environ.pop("AIRFARE_TEST_VALUE", None)
