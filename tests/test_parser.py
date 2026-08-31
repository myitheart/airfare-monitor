from __future__ import annotations

import copy
import unittest
from datetime import date, datetime, time
from decimal import Decimal

from airfare_monitor.errors import IncompleteResponseError
from airfare_monitor.models import EtdWindow, LegConfig
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

    def test_filters_connections_and_sorts_by_total(self):
        flights, observed, eligible = parse_completed_payload(payload(), leg(), datetime(2026, 8, 31, 12))
        self.assertEqual(observed, 3)
        self.assertEqual(eligible, 2)
        self.assertEqual([item.total_price_cny for item in flights], [Decimal("800"), Decimal("880")])
        self.assertTrue(all(item.segment_count == 1 for item in flights))

    def test_signature_changes_when_schedule_changes(self):
        first, _, _ = parse_completed_payload(payload(), leg(), datetime(2026, 8, 31, 12))
        changed = copy.deepcopy(payload())
        changed["result"]["flightList"][1]["flightSegments"][0]["departureDateTime"] = "2026-09-27 09:10"
        second, _, _ = parse_completed_payload(changed, leg(), datetime(2026, 8, 31, 12))
        self.assertNotEqual(first[0].flight_signature, second[0].flight_signature)

    def test_deduplicates_same_itinerary_and_keeps_cheapest_offer(self):
        value = payload()
        duplicate = copy.deepcopy(value["result"]["flightList"][0])
        duplicate["price"]["lowTotalPrice"] = 850
        value["result"]["flightList"].append(duplicate)
        flights, observed, eligible = parse_completed_payload(value, leg(), datetime(2026, 8, 31, 12))
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
                            "seatInfo": {"nums": 9},
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
        flights, observed, eligible = parse_completed_payload(actual_shape, leg(), datetime(2026, 8, 31, 12))
        self.assertEqual((observed, eligible), (1, 1))
        self.assertEqual(flights[0].origin_airport_iata, "PVG")
        self.assertEqual(flights[0].total_price_cny, Decimal("1450"))
        self.assertEqual(flights[0].remaining_seats, "9")
