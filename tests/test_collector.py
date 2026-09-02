from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

from airfare_monitor.collector import build_roundtrip_search_url
from airfare_monitor.models import EtdWindow, LegConfig


class CollectorUrlTests(unittest.TestCase):
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
