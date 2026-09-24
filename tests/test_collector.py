from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

from airfare_monitor.collector import _candidate_leg, _selected_code, build_roundtrip_search_url
from airfare_monitor.config import MAX_STORED_CANDIDATES
from airfare_monitor.models import EtdWindow, LegConfig


class SuggestionConfirmationTests(unittest.TestCase):
    def test_selected_code_extracts_code_from_suggestion_value(self):
        self.assertEqual(_selected_code("上海(SHA)"), "SHA")
        self.assertEqual(_selected_code("东京(TYO)"), "TYO")
        self.assertEqual(_selected_code("伦敦(LON)"), "LON")

    def test_selected_code_matches_configured_airport_code(self):
        self.assertEqual(_selected_code("吉隆坡(KUL)"), "KUL")
        self.assertEqual(_selected_code("北京(PEK)"), "PEK")

    def test_selected_code_returns_none_without_code(self):
        self.assertIsNone(_selected_code("上海"))
        self.assertIsNone(_selected_code(""))
        self.assertIsNone(_selected_code(None))


class CollectorUrlTests(unittest.TestCase):
    def test_candidate_parse_limit_does_not_change_report_top_n(self):
        configured = LegConfig(
            "candidate-limit",
            True,
            "PVG",
            "KUL",
            date(2026, 9, 27),
            EtdWindow(time(0), time(23, 59)),
            True,
            None,
            10,
            1,
            0,
            "economy",
        )

        expanded = _candidate_leg(configured)

        self.assertEqual(configured.top_n, 10)
        self.assertEqual(expanded.top_n, MAX_STORED_CANDIDATES)

    def test_round_trip_url_contains_both_dates_and_route(self):
        leg = LegConfig(
            "roundtrip-kul-mle",
            True,
            "KUL",
            "MLE",
            date(2026, 9, 27),
            EtdWindow(time(6), time(11, 59)),
            False,
            Decimal("3000"),
            10,
            1,
            0,
            "economy",
            "吉隆坡",
            "马累",
        )
        leg = replace(
            leg,
            return_date=date(2026, 10, 2),
            return_etd_window=EtdWindow(time(12), time(23, 59)),
        )
        template = (
            "https://example.test?from={origin}&to={destination}&fromCity={origin_name}"
            "&toCity={destination_name}&fromDate={date}&toDate={return_date}"
            "&adult={adult_count}&child={child_count}&cabin={cabin_class}"
        )
        url = build_roundtrip_search_url(template, leg)
        self.assertIn("from=KUL", url)
        self.assertIn("to=MLE", url)
        self.assertIn("fromDate=2026-09-27", url)
        self.assertIn("toDate=2026-10-02", url)
        self.assertIn("fromCity=%E5%90%89%E9%9A%86%E5%9D%A1", url)
        self.assertIn("toCity=%E9%A9%AC%E7%B4%AF", url)


if __name__ == "__main__":
    unittest.main()
