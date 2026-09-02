from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal

from airfare_monitor.errors import IncompleteResponseError
from airfare_monitor.models import EtdWindow, LegConfig, PreferredSchedule
from airfare_monitor.parser import parse_completed_payload


def leg() -> LegConfig:
    return LegConfig(
        id="leg-1",
        enabled=True,
        origin_airport_iata="SHA",
        destination_airport_iata="KUL",
        departure_date=date(2026, 9, 27),
        etd_window=EtdWindow(time(8), time(18)),
        direct_only=True,
        expected_total_price_cny=Decimal("1000"),
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
    )


def segment(code: str, origin: str, destination: str, departure: str, arrival: str) -> dict:
    return {
        "flightCode": code,
        "carrierCode": code[:2],
        "depAirportCode": origin,
        "arrAirportCode": destination,
        "departureDateTime": departure,
        "arrivalDateTime": arrival,
    }


def payload() -> dict:
    return {
        "result": {
            "ctrlInfo": {"completed": True, "queryId": "query-1"},
            "flightList": [
                {
                    "flightSegments": [segment("MU123", "SHA", "KUL", "2026-09-27 10:00", "2026-09-27 15:30")],
                    "price": {"lowPrice": 700, "tax": 180, "lowTotalPrice": 880, "currencyCode": "CNY"},
                    "remainingSeats": "4张",
                    "baggage": {"piece": 1, "weight": "23kg"},
                    "sourceDomain": "example.test",
                },
                {
                    "flightSegments": [segment("MU456", "SHA", "KUL", "2026-09-27 09:00", "2026-09-27 14:30")],
                    "price": {"lowPrice": 600, "tax": 200, "lowTotalPrice": 800, "currencyCode": "CNY"},
                },
                {
                    "flightSegments": [
                        segment("MU100", "SHA", "CAN", "2026-09-27 11:00", "2026-09-27 13:00"),
                        segment("CZ200", "CAN", "KUL", "2026-09-27 15:00", "2026-09-27 19:00"),
                    ],
                    "price": {"lowPrice": 300, "tax": 100, "lowTotalPrice": 400, "currencyCode": "CNY"},
                },
            ],
        }
    }


class ParserTests(unittest.TestCase):
    def test_requires_completed_true(self):
        value = payload()
        value["result"]["ctrlInfo"]["completed"] = False
        with self.assertRaises(IncompleteResponseError):
            parse_completed_payload(value, leg(), datetime(2026, 8, 31, 12))

    def test_accepts_completed_empty_flight_prices(self):
        value = {"result": {"ctrlInfo": {"completed": True}, "flightPrices": {}}}
        flights, preferred, observed, eligible = parse_completed_payload(
            value, leg(), datetime(2026, 8, 31, 12)
        )
        self.assertEqual((flights, preferred, observed, eligible), ([], [], 0, 0))

    def test_filters_connections_and_sorts_by_total(self):
        flights, preferred, observed, eligible = parse_completed_payload(payload(), leg(), datetime(2026, 8, 31, 12))
        self.assertEqual(observed, 3)
        self.assertEqual(eligible, 2)
        self.assertEqual(preferred, [])
        self.assertEqual([item.total_price_cny for item in flights], [Decimal("800"), Decimal("880")])
        self.assertTrue(all(item.segment_count == 1 for item in flights))

    def test_includes_connection_within_configured_total_layover(self):
        configured = replace(leg(), direct_only=False, max_layover_minutes=120)
        flights, _, observed, eligible = parse_completed_payload(
            payload(), configured, datetime(2026, 8, 31, 12)
        )
        self.assertEqual((observed, eligible), (3, 3))
        self.assertEqual(flights[0].total_price_cny, Decimal("400"))
        self.assertEqual(flights[0].flight_codes, ("MU100", "CZ200"))
        self.assertEqual(flights[0].connection_airports, ("CAN",))
        self.assertEqual(flights[0].layover_minutes, 120)
        self.assertFalse(flights[0].is_direct)

    def test_filters_connection_exceeding_total_layover_limit(self):
        configured = replace(leg(), direct_only=False, max_layover_minutes=119)
        flights, _, _, eligible = parse_completed_payload(
            payload(), configured, datetime(2026, 8, 31, 12)
        )
        self.assertEqual(eligible, 2)
        self.assertTrue(all(item.is_direct for item in flights))

    def test_signature_changes_when_schedule_changes(self):
        first, _, _, _ = parse_completed_payload(payload(), leg(), datetime(2026, 8, 31, 12))
        changed = copy.deepcopy(payload())
        changed["result"]["flightList"][1]["flightSegments"][0]["departureDateTime"] = "2026-09-27 09:10"
        second, _, _, _ = parse_completed_payload(changed, leg(), datetime(2026, 8, 31, 12))
        self.assertNotEqual(first[0].flight_signature, second[0].flight_signature)

    def test_deduplicates_same_itinerary_and_keeps_cheapest_offer(self):
        value = payload()
        duplicate = copy.deepcopy(value["result"]["flightList"][0])
        duplicate["price"]["lowTotalPrice"] = 850
        value["result"]["flightList"].append(duplicate)
        flights, _, observed, eligible = parse_completed_payload(value, leg(), datetime(2026, 8, 31, 12))
        self.assertEqual(observed, 4)
        self.assertEqual(eligible, 2)
        self.assertEqual([flight.total_price_cny for flight in flights], [Decimal("800"), Decimal("850")])

    def test_parses_observed_qunar_flight_prices_shape_and_city_code(self):
        actual_shape = {
            "result": {
                "ctrlInfo": {"completed": True},
                "flightPrices": {
                    "9C6515": {
                        "journey": {
                            "depCityCode": "SHA",
                            "arrCityCode": "KUL",
                            "duration": 350,
                            "seatInfo": {"nums": 9, "showOTxt": "", "showLTxt": ""},
                            "trips": [
                                {
                                    "flightSegments": [
                                        {
                                            "code": "9C6515",
                                            "carrierCode": "9C",
                                            "depAirportCode": "PVG",
                                            "arrAirportCode": "KUL",
                                            "depDate": "2026-09-27",
                                            "depTime": "16:45",
                                            "arrDate": "2026-09-27",
                                            "arrTime": "22:35",
                                        }
                                    ]
                                }
                            ],
                        },
                        "price": {
                            "lowPrice": 1000,
                            "tax": 450,
                            "lowTotalPrice": 1450,
                            "currencyCode": "CNY",
                            "domain": "example.test",
                            "freeCheckLuggagePiece": 0,
                            "freeCheckLuggageWeight": 0,
                        },
                    }
                },
            }
        }
        flights, _, observed, eligible = parse_completed_payload(actual_shape, leg(), datetime(2026, 8, 31, 12))
        self.assertEqual((observed, eligible), (1, 1))
        self.assertEqual(flights[0].origin_airport_iata, "PVG")
        self.assertEqual(flights[0].total_price_cny, Decimal("1450"))
        self.assertEqual(flights[0].remaining_seats, "9")
        self.assertEqual(flights[0].seat_availability.display(), "9张或以上（平台提示）")

    def test_retains_preferred_schedule_outside_top_n(self):
        preference = PreferredSchedule(
            label="上海 → 吉隆坡",
            departure_time=time(10),
            arrival_time=time(15, 30),
            origin_airport_iata="SHA",
            destination_airport_iata="KUL",
        )
        configured = replace(leg(), top_n=1, preferred_schedules=(preference,))
        flights, preferred, _, eligible = parse_completed_payload(
            payload(), configured, datetime(2026, 8, 31, 12)
        )
        self.assertEqual(eligible, 2)
        self.assertEqual([flight.total_price_cny for flight in flights], [Decimal("800")])
        self.assertIsNotNone(preferred[0])
        self.assertEqual(preferred[0].total_price_cny, Decimal("880"))

    def test_matches_preferred_schedule_arriving_next_day(self):
        value = payload()
        value["result"]["flightList"] = [
            {
                "flightSegments": [
                    segment("MU789", "SHA", "KUL", "2026-09-27 22:30", "2026-09-28 03:00")
                ],
                "price": {"lowTotalPrice": 900, "currencyCode": "CNY"},
            }
        ]
        preference = PreferredSchedule(
            label="上海 → 吉隆坡",
            departure_time=time(22, 30),
            arrival_time=time(3),
            arrival_day_offset=1,
        )
        configured = replace(
            leg(),
            etd_window=EtdWindow(time(0), time(23, 59)),
            preferred_schedules=(preference,),
        )
        _, preferred, _, _ = parse_completed_payload(value, configured, datetime(2026, 8, 31, 12))
        self.assertIsNotNone(preferred[0])
        self.assertEqual(preferred[0].eta_local, datetime(2026, 9, 28, 3))

    def test_preferred_tolerance_chooses_closest_schedule_before_price(self):
        preference = PreferredSchedule(
            label="上海 → 吉隆坡",
            departure_time=time(9, 50),
            arrival_time=time(15, 20),
            departure_tolerance_minutes=60,
            arrival_tolerance_minutes=60,
        )
        configured = replace(leg(), preferred_schedules=(preference,))
        _, preferred, _, _ = parse_completed_payload(payload(), configured, datetime(2026, 8, 31, 12))
        self.assertIsNotNone(preferred[0])
        self.assertEqual(preferred[0].flight_codes, ("MU123",))
        self.assertEqual(preferred[0].total_price_cny, Decimal("880"))

    def test_parses_round_trip_combination_total_and_both_directions(self):
        configured = replace(
            leg(),
            expected_total_price_cny=None,
            return_date=date(2026, 10, 3),
            return_etd_window=EtdWindow(time(12), time(23, 59)),
            return_direct_only=True,
        )
        value = {
            "result": {
                "ctrlInfo": {"completed": True},
                "flightPrices": {
                    "MU001_MU002": {
                        "journey": {
                            "journeyType": "ROUNDTRIP",
                            "depCityCode": "SHA",
                            "arrCityCode": "KUL",
                            "seatInfo": {"nums": 2, "showOTxt": "2张", "showLTxt": "票少"},
                            "ticketInsufficient": False,
                            "trips": [
                                {
                                    "depCityCode": "SHA",
                                    "arrCityCode": "KUL",
                                    "duration": 330,
                                    "seatInfo": {"nums": 9, "showOTxt": "", "showLTxt": ""},
                                    "flightSegments": [
                                        segment("MU001", "PVG", "KUL", "2026-09-27 08:00", "2026-09-27 13:30")
                                    ],
                                },
                                {
                                    "depCityCode": "KUL",
                                    "arrCityCode": "SHA",
                                    "duration": 320,
                                    "seatInfo": {"nums": 2, "showOTxt": "2张", "showLTxt": "票少"},
                                    "flightSegments": [
                                        segment("MU002", "KUL", "PVG", "2026-10-03 14:00", "2026-10-03 19:20")
                                    ],
                                },
                            ],
                        },
                        "price": {
                            "lowPrice": 1200,
                            "tax": 600,
                            "lowTotalPrice": 1800,
                            "currencyCode": "CNY",
                        },
                    }
                },
            }
        }
        flights, _, observed, eligible = parse_completed_payload(
            value, configured, datetime(2026, 9, 2, 10)
        )
        self.assertEqual((observed, eligible), (1, 1))
        self.assertEqual(flights[0].total_price_cny, Decimal("1800"))
        self.assertEqual(flights[0].flight_codes, ("MU001",))
        self.assertIsNotNone(flights[0].return_itinerary)
        self.assertEqual(flights[0].return_itinerary.flight_codes, ("MU002",))
        self.assertEqual(flights[0].return_itinerary.destination_airport_iata, "PVG")
        self.assertEqual(flights[0].seat_availability.display(), "2张（票少）")
        self.assertEqual(
            flights[0].outbound_seat_availability.display(), "9张或以上（平台提示）"
        )
        self.assertEqual(flights[0].return_itinerary.seat_availability.display(), "2张（票少）")

        outside_window = copy.deepcopy(value)
        return_segment = outside_window["result"]["flightPrices"]["MU001_MU002"]["journey"]["trips"][1][
            "flightSegments"
        ][0]
        return_segment["departureDateTime"] = "2026-10-03 10:00"
        return_segment["arrivalDateTime"] = "2026-10-03 15:20"
        filtered, _, _, filtered_count = parse_completed_payload(
            outside_window, configured, datetime(2026, 9, 2, 10)
        )
        self.assertEqual(filtered_count, 0)
        self.assertEqual(filtered, [])
