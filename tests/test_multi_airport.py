from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from airfare_monitor.config import load_routes
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.market import resolve_market
from airfare_monitor.models import EtdWindow, LegConfig, PreferredSchedule
from airfare_monitor.parser import parse_completed_payload


ROOT = Path(__file__).resolve().parents[1]


def _make_leg(
    *,
    origin: str,
    destination: str,
    origin_airports: tuple[str, ...] | None = None,
    destination_airports: tuple[str, ...] | None = None,
    direct_only: bool = True,
) -> LegConfig:
    return LegConfig(
        id="test-leg",
        enabled=True,
        origin_airport_iata=origin,
        destination_airport_iata=destination,
        departure_date=date(2026, 10, 2),
        etd_window=EtdWindow(time(0, 0), time(23, 59)),
        direct_only=direct_only,
        expected_total_price_cny=None,
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        origin_airports=origin_airports,
        destination_airports=destination_airports,
    )


class MultiAirportCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = AirportCatalog.load(ROOT / "resources" / "airports.zh.json")

    def test_catalog_search_without_cities_keeps_physical_airports_only(self):
        results = self.catalog.search("上海")
        self.assertEqual({item.airport_iata for item in results}, {"SHA", "PVG"})
        self.assertTrue(all(not item.is_city for item in results))

    def test_catalog_search_with_cities_ranks_metro_city_first(self):
        results = self.catalog.search("上海", include_cities=True)
        self.assertGreaterEqual(len(results), 3)
        self.assertEqual(results[0].airport_iata, "SHA")
        self.assertTrue(results[0].is_city)
        self.assertEqual(results[0].child_airports, ("PVG", "SHA"))

    def test_catalog_by_city_retrieves_city_aggregate_record(self):
        city = self.catalog.by_city("TYO")
        self.assertIsNotNone(city)
        assert city is not None
        self.assertTrue(city.is_city)
        self.assertEqual(city.child_airports, ("NRT", "HND"))

    def test_catalog_by_iata_prefers_physical_airport(self):
        physical = self.catalog.by_iata("SHA")
        self.assertIsNotNone(physical)
        assert physical is not None
        self.assertFalse(physical.is_city)
        self.assertEqual(physical.display_name_zh, "上海虹桥")


class MultiAirportMarketTests(unittest.TestCase):
    def test_metro_city_codes_resolve_correct_country(self):
        # BJS (北京全城) -> CN, TYO (东京全城) -> JP => international
        leg_intl = _make_leg(origin="BJS", destination="TYO")
        self.assertEqual(resolve_market(leg_intl), "international")

        # SHA (上海) -> CTU (成都) => domestic
        leg_dom = _make_leg(origin="SHA", destination="CTU")
        self.assertEqual(resolve_market(leg_dom), "domestic")


class MultiAirportConfigAndRepoTests(unittest.TestCase):
    def test_route_repository_serializes_and_reloads_multi_airport(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_path = Path(temporary) / "routes.yaml"
            repo = RouteRepository(repo_path)
            original = _make_leg(
                origin="SHA",
                destination="TYO",
                origin_airports=("SHA", "PVG"),
                destination_airports=("NRT", "HND"),
            )
            repo.save([original])

            reloaded = repo.load()
            self.assertEqual(len(reloaded), 1)
            leg = reloaded[0]
            self.assertEqual(leg.origin_airport_iata, "SHA")
            self.assertEqual(leg.destination_airport_iata, "TYO")
            self.assertEqual(leg.origin_airports, ("SHA", "PVG"))
            self.assertEqual(leg.destination_airports, ("NRT", "HND"))
            self.assertEqual(leg.allowed_origin_airports, {"SHA", "PVG"})
            self.assertEqual(leg.allowed_destination_airports, {"TYO", "NRT", "HND"})

    def test_single_airport_leg_falls_back_to_singleton_set(self):
        single = _make_leg(origin="SHA", destination="NRT")
        self.assertEqual(single.allowed_origin_airports, {"SHA"})
        self.assertEqual(single.allowed_destination_airports, {"NRT"})


class MultiAirportParserFilterTests(unittest.TestCase):
    def _mock_payload(self) -> dict:
        return {
            "result": {
                "ctrlInfo": {"completed": True, "queryId": "query-multi"},
                "flightList": [
                    {
                        "flightSegments": [
                            {
                                "flightCode": "MU521",
                                "carrierCode": "MU",
                                "depAirportCode": "PVG",
                                "arrAirportCode": "NRT",
                                "departureDateTime": "2026-10-02 10:00",
                                "arrivalDateTime": "2026-10-02 14:00",
                            }
                        ],
                        "price": {"lowTotalPrice": 2000, "currencyCode": "CNY"},
                    },
                    {
                        "flightSegments": [
                            {
                                "flightCode": "FM895",
                                "carrierCode": "FM",
                                "depAirportCode": "SHA",
                                "arrAirportCode": "HND",
                                "departureDateTime": "2026-10-02 11:00",
                                "arrivalDateTime": "2026-10-02 15:00",
                            }
                        ],
                        "price": {"lowTotalPrice": 1800, "currencyCode": "CNY"},
                    },
                ],
            }
        }

    def test_single_airport_strategy_strictly_filters_out_other_airports(self):
        # 用户指定单机场：仅接受虹桥 SHA -> 成田 NRT
        single_leg = _make_leg(origin="SHA", destination="NRT")
        payload = self._mock_payload()
        flights, _, observed, eligible = parse_completed_payload(payload, single_leg, datetime.now())
        self.assertEqual(observed, 2)
        # FL01 起飞是 PVG（≠SHA），FL02 到达是 HND（≠NRT），因此单机场策略下 0 条合格
        self.assertEqual(eligible, 0)
        self.assertEqual(len(flights), 0)

    def test_multi_airport_strategy_accepts_both_airports_and_ranks_by_price(self):
        # 用户选择全城聚合：上海 (PVG/SHA) -> 东京 (NRT/HND)
        multi_leg = _make_leg(
            origin="SHA",
            destination="TYO",
            origin_airports=("SHA", "PVG"),
            destination_airports=("NRT", "HND"),
        )
        payload = self._mock_payload()
        flights, _, observed, eligible = parse_completed_payload(payload, multi_leg, datetime.now())
        self.assertEqual(observed, 2)
        # 两个航班都在允许的机场集合内，全部合格！
        self.assertEqual(eligible, 2)
        self.assertEqual(len(flights), 2)
        # 按价格升序排列：¥1800 (SHA->HND) 排在 ¥2000 (PVG->NRT) 前面
        self.assertEqual(flights[0].total_price_cny, Decimal("1800"))
        self.assertEqual(flights[0].origin_airport_iata, "SHA")
        self.assertEqual(flights[0].destination_airport_iata, "HND")

        self.assertEqual(flights[1].total_price_cny, Decimal("2000"))
        self.assertEqual(flights[1].origin_airport_iata, "PVG")
        self.assertEqual(flights[1].destination_airport_iata, "NRT")


if __name__ == "__main__":
    unittest.main()


class TongchengCityCodeCompletionTests(unittest.TestCase):
    """回归:同程页面完成态回填城市码(如 BJS)时必须判定完成。"""

    def _leg(self) -> LegConfig:
        return _make_leg(
            origin="BJS",
            destination="CTU",
            origin_airports=("PEK", "PKX"),
            destination_airports=("CTU", "TFU"),
        )

    def test_page_state_with_city_code_completes(self):
        from airfare_monitor.tongcheng_parser import is_completed_tongcheng_page_state

        state = {
            "Departure": "BJS",
            "Arrival": "CTU",
            "DepartureDate": "2026-10-02",
            "dataflag": "last",
            "flightLists": [{"x": 1}],
        }
        self.assertTrue(is_completed_tongcheng_page_state(state, self._leg()))

    def test_api_payload_with_city_code_completes(self):
        from airfare_monitor.tongcheng_parser import is_completed_tongcheng_payload

        payload = {
            "resCode": 0,
            "apiSuccess": True,
            "apiCode": 0,
            "body": {
                "FlyOffCityCode": "BJS",
                "ArriveCityCode": "CTU",
                "FlyOffTime": "2026-10-02 08:45",
                "ErrorCode": 0,
                "paging": {"dataflag": "all"},
                "FlightInfoSimpleList": [{"x": 1}],
                "FlightNum": 1,
            },
        }
        self.assertTrue(is_completed_tongcheng_payload(payload, self._leg()))

    def test_single_airport_still_rejects_other_city(self):
        from airfare_monitor.tongcheng_parser import is_completed_tongcheng_page_state

        single = _make_leg(origin="SHA", destination="XMN")
        state = {
            "Departure": "PVG",  # 上海全城码但对单机场航程是别的机场
            "Arrival": "XMN",
            "DepartureDate": "2026-10-02",
            "dataflag": "last",
            "flightLists": [],
        }
        self.assertFalse(is_completed_tongcheng_page_state(state, single))


class PreferredScheduleCityWildcardTests(unittest.TestCase):
    """回归:全城航程的重点班次按时刻匹配(机场字段为 None),单机场仍精确。"""

    def test_city_preferred_matches_real_airport_flight(self):
        from airfare_monitor.models import FlightSnapshot
        from airfare_monitor.ranking import rank_flights

        leg = _make_leg(
            origin="BJS",
            destination="TYO",
            origin_airports=("PEK", "PKX"),
            destination_airports=("NRT", "HND"),
        )
        leg = replace(
            leg,
            preferred_schedules=(
                PreferredSchedule(
                    label="早班机",
                    departure_time=time(9, 0),
                    arrival_time=time(13, 30),
                    departure_tolerance_minutes=60,
                    arrival_tolerance_minutes=60,
                    origin_airport_iata=None,
                    destination_airport_iata=None,
                ),
            ),
        )
        moment = datetime(2026, 10, 2, 9, 5)
        flight = FlightSnapshot(
            flight_signature="sig", flight_codes=("CA1",), carrier_codes=("CA",),
            origin_airport_iata="PEK", destination_airport_iata="HND",
            departure_date=date(2026, 10, 2), etd_local=moment, eta_local=datetime(2026, 10, 2, 13, 35),
            duration_minutes=None, segment_count=1, is_direct=True,
            base_price_cny=None, tax_cny=None, total_price_cny=Decimal("2000"), currency_code="CNY",
            remaining_seats=None, free_baggage_piece=None, free_baggage_weight=None,
            source_domain=None, captured_at=moment,
        )
        _, matches, _ = rank_flights([flight], leg)
        self.assertIsNotNone(matches[0])
