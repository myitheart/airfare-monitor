from __future__ import annotations

import unittest
from datetime import date, datetime, time
from decimal import Decimal

from airfare_monitor.errors import IncompleteResponseError
from airfare_monitor.models import EtdWindow, LegConfig, PreferredSchedule
from airfare_monitor.tongcheng_parser import parse_tongcheng_page_state, parse_tongcheng_payload


def leg() -> LegConfig:
    return LegConfig(
        id="domestic-1",
        enabled=True,
        origin_airport_iata="SHA",
        destination_airport_iata="XMN",
        departure_date=date(2026, 9, 23),
        etd_window=EtdWindow(time(0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=Decimal("500"),
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        market="domestic",
        preferred_schedules=(
            PreferredSchedule(
                label="上海虹桥 → 厦门",
                departure_time=time(7, 20),
                arrival_time=time(9, 10),
                departure_tolerance_minutes=15,
                arrival_tolerance_minutes=15,
                origin_airport_iata="SHA",
                destination_airport_iata="XMN",
            ),
        ),
    )


def flight(no: str, departure: str, arrival: str, fare: int, *, stop_num: int = 0) -> dict:
    return {
        "flightNo": no,
        "airCompanyCode": no[:2],
        "departureCityCode": "SHA",
        "arrivalCityCode": "XMN",
        "originAirportCode": "SHA",
        "arriveAirportCode": "XMN",
        "flyOffTime": departure,
        "arrivalTime": arrival,
        "spantime": "1小时50分钟",
        "stopNum": stop_num,
        "lcp": fare,
        "pt": 50,
        "ot": 70,
        "availableTickets": 0,
    }


def payload() -> dict:
    return {
        "resCode": 0,
        "apiSuccess": True,
        "apiCode": 0,
        "body": {
            "ErrorCode": 0,
            "FlyOffCityCode": "SHA",
            "ArriveCityCode": "XMN",
            "FlyOffTime": "2026-09-23",
            "FlightNum": 3,
            "paging": {"dataflag": "all"},
            "FlightInfoSimpleList": [
                flight("9C8815", "2026-09-23 07:15", "2026-09-23 09:05", 350),
                flight("9C8803", "2026-09-23 19:15", "2026-09-23 21:10", 370),
                flight("MU100", "2026-09-23 08:00", "2026-09-23 12:00", 200, stop_num=1),
            ],
        },
    }


class TongchengParserTests(unittest.TestCase):
    def test_parses_final_direct_url_page_state(self):
        value = payload()
        state = {
            "flightLists": value["body"]["FlightInfoSimpleList"],
            "dataflag": "last",
            "Departure": "SHA",
            "Arrival": "XMN",
            "DepartureDate": "2026-09-23",
        }
        flights, _, observed, eligible = parse_tongcheng_page_state(
            state, leg(), datetime(2026, 9, 1, 12)
        )
        self.assertEqual((observed, eligible), (3, 2))
        self.assertEqual(flights[0].total_price_cny, Decimal("470"))

    def test_requires_all_page_and_matching_route_date(self):
        value = payload()
        value["body"]["paging"]["dataflag"] = "some"
        with self.assertRaises(IncompleteResponseError):
            parse_tongcheng_payload(value, leg(), datetime(2026, 9, 1, 12))

    def test_adds_airport_and_fuel_taxes_to_alert_total(self):
        flights, preferred, observed, eligible = parse_tongcheng_payload(
            payload(), leg(), datetime(2026, 9, 1, 12)
        )
        self.assertEqual((observed, eligible), (3, 2))
        self.assertEqual([item.base_price_cny for item in flights], [Decimal("350"), Decimal("370")])
        self.assertEqual([item.tax_cny for item in flights], [Decimal("120"), Decimal("120")])
        self.assertEqual([item.total_price_cny for item in flights], [Decimal("470"), Decimal("490")])
        self.assertEqual(flights[0].source_domain, "ly.com")
        self.assertEqual(flights[0].duration_minutes, 110)
        self.assertIsNone(flights[0].remaining_seats)
        self.assertEqual(preferred[0].flight_codes, ("9C8815",))

    def test_rejects_completed_response_for_another_route(self):
        value = payload()
        value["body"]["ArriveCityCode"] = "PEK"
        with self.assertRaises(IncompleteResponseError):
            parse_tongcheng_payload(value, leg(), datetime(2026, 9, 1, 12))

    def test_rejects_truncated_flight_list(self):
        value = payload()
        value["body"]["FlightInfoSimpleList"].pop()
        with self.assertRaises(IncompleteResponseError):
            parse_tongcheng_payload(value, leg(), datetime(2026, 9, 1, 12))
