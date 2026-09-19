"""行李价格级：解析提取、入库迁移与展示逻辑的测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from airfare_monitor.config import LegConfig
from airfare_monitor.models import (
    EtdWindow,
    FlightSnapshot,
    LegResult,
    LegStatus,
    RunReport,
    RunStatus,
)
from airfare_monitor.parser import parse_completed_payload
from airfare_monitor.storage import SQLiteStore
from airfare_monitor.ui.flight_results_page import _baggage_and_seats, _luggage_price_tiers


def _leg() -> LegConfig:
    return LegConfig(
        id="leg",
        enabled=True,
        origin_airport_iata="PVG",
        destination_airport_iata="NRT",
        departure_date=date(2026, 11, 26),
        etd_window=EtdWindow(time(0), time(23, 59)),
        direct_only=False,
        expected_total_price_cny=None,
        top_n=5,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
    )


def _snapshot(**overrides: object) -> FlightSnapshot:
    values: dict[str, object] = {
        "flight_signature": "SIG",
        "flight_codes": ("MU523",),
        "carrier_codes": ("MU",),
        "origin_airport_iata": "PVG",
        "destination_airport_iata": "NRT",
        "departure_date": date(2026, 11, 26),
        "etd_local": datetime(2026, 11, 26, 9, 10),
        "eta_local": datetime(2026, 11, 26, 12, 50),
        "duration_minutes": 160,
        "segment_count": 1,
        "is_direct": True,
        "base_price_cny": Decimal("1470"),
        "tax_cny": Decimal("1060"),
        "total_price_cny": Decimal("2530"),
        "currency_code": "CNY",
        "remaining_seats": "9",
        "free_baggage_piece": 0,
        "free_baggage_weight": "0",
        "source_domain": "qunar.com",
        "captured_at": datetime(2026, 11, 26, 8, 0),
    }
    values.update(overrides)
    return FlightSnapshot(**values)  # type: ignore[arg-type]


def _payload(extra_price: dict) -> dict:
    return {
        "result": {
            "ctrlInfo": {"completed": True},
            "flightList": [
                {
                    "flightSegments": [
                        {
                            "flightCode": "MU523",
                            "carrierCode": "MU",
                            "depAirportCode": "PVG",
                            "arrAirportCode": "NRT",
                            "departureDateTime": "2026-11-26 09:10",
                            "arrivalDateTime": "2026-11-26 12:50",
                        }
                    ],
                    "price": {
                        "lowPrice": 1470,
                        "tax": 1060,
                        "lowTotalPrice": 2530,
                        "currencyCode": "CNY",
                        **extra_price,
                    },
                }
            ],
        }
    }


class LuggageTierDisplayTests(unittest.TestCase):
    def test_zero_allowance_without_inclusive_price(self):
        text = _baggage_and_seats({"free_baggage_piece": 0, "free_baggage_weight": "0"})
        self.assertIn("本价不含免费托运行李", text)
        self.assertNotIn("含行李最低档", text)

    def test_zero_allowance_with_inclusive_price(self):
        text = _baggage_and_seats(
            {
                "free_baggage_piece": 0,
                "free_baggage_weight": "0",
                "total_price_cny": "2530",
                "luggage_inclusive_price_cny": "2730",
            }
        )
        self.assertIn("本价不含免费托运行李", text)
        self.assertIn("含行李最低档 ¥2,730（+¥200）", text)

    def test_free_allowance_included_in_fare(self):
        text = _baggage_and_seats({"free_baggage_piece": 2, "free_baggage_weight": "23KG"})
        self.assertIn("本价含免费托运 2 件 / 23KG", text)
        self.assertNotIn("含行李最低档", text)

    def test_missing_data_reports_absent(self):
        self.assertEqual(_luggage_price_tiers({}), [])
        self.assertIn("行李额未提供", _baggage_and_seats({}))


class LuggageParseTests(unittest.TestCase):
    def test_parse_extracts_inclusive_luggage_price(self):
        flights, _, _, _ = parse_completed_payload(
            _payload({"lowestPriceWithFreeLuggage": 2730}), _leg(), datetime.now()
        )
        self.assertEqual(len(flights), 1)
        self.assertEqual(flights[0].luggage_inclusive_price_cny, Decimal("2730"))

    def test_parse_tolerates_null_inclusive_luggage_price(self):
        flights, _, _, _ = parse_completed_payload(
            _payload({"lowestPriceWithFreeLuggage": None}), _leg(), datetime.now()
        )
        self.assertEqual(len(flights), 1)
        self.assertIsNone(flights[0].luggage_inclusive_price_cny)


class LuggageStorageTests(unittest.TestCase):
    def test_roundtrip_persists_inclusive_price(self):
        with TemporaryDirectory() as temporary:
            store = SQLiteStore(Path(temporary) / "db.sqlite3")
            store.initialize()
            leg = _leg()
            flight = _snapshot(luggage_inclusive_price_cny=Decimal("2730"))
            captured = datetime(2026, 11, 26, 8, 0)
            result = LegResult(
                leg, LegStatus.SUCCESS, captured, flights=[flight], completed_response=True
            )
            store.save_report(
                RunReport("r1", captured, captured, RunStatus.SUCCESS, [result], set())
            )
            payload = store.latest_flight_candidates("leg")
            assert payload is not None
            stored = payload["flights"][0]
            self.assertEqual(stored["luggage_inclusive_price_cny"], "2730")
            self.assertIn("含行李最低档 ¥2,730", _baggage_and_seats(stored))

    def test_legacy_rows_without_column_render_zero_allowance(self):
        """旧数据缺列（NULL）且免费额为 0：展示走“不含免费托运”档。"""
        text = _baggage_and_seats(
            {
                "free_baggage_piece": 0,
                "free_baggage_weight": "0",
                "luggage_inclusive_price_cny": None,
            }
        )
        self.assertIn("本价不含免费托运行李", text)


if __name__ == "__main__":
    unittest.main()
