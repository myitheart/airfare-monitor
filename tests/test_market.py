from __future__ import annotations

import unittest
from datetime import date, time
from decimal import Decimal

from airfare_monitor.errors import CollectionError
from airfare_monitor.market import resolve_market
from airfare_monitor.models import EtdWindow, LegConfig


def leg(origin: str, destination: str, *, market: str = "auto") -> LegConfig:
    return LegConfig(
        id="leg-1",
        enabled=True,
        origin_airport_iata=origin,
        destination_airport_iata=destination,
        departure_date=date(2026, 9, 23),
        etd_window=EtdWindow(time(0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=Decimal("1000"),
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        market=market,
    )


class MarketTests(unittest.TestCase):
    def test_auto_routes_mainland_domestic_to_tongcheng(self):
        self.assertEqual(resolve_market(leg("SHA", "XMN")), "domestic")

    def test_auto_routes_cross_border_to_qunar(self):
        self.assertEqual(resolve_market(leg("SHA", "KUL")), "international")

    def test_explicit_override_wins(self):
        self.assertEqual(resolve_market(leg("SHA", "XMN", market="international")), "international")

    def test_unknown_code_requires_explicit_market(self):
        with self.assertRaisesRegex(CollectionError, "market"):
            resolve_market(leg("QQQ", "XMN"))
