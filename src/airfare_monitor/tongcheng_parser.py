"""Parse complete Tongcheng domestic-flight responses captured by Chromium."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import IncompleteResponseError, ParseError
from .models import FlightSnapshot, LegConfig
from .ranking import rank_flights


def _body(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get("body")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def is_completed_tongcheng_payload(payload: object, leg: LegConfig | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    body = _body(payload)
    if body is None:
        return False
    paging = body.get("paging")
    flights = body.get("FlightInfoSimpleList")
    complete = (
        payload.get("resCode") == 0
        and payload.get("apiSuccess") is True
        and payload.get("apiCode") == 0
        and body.get("ErrorCode") == 0
        and isinstance(paging, dict)
        and paging.get("dataflag") == "all"
        and isinstance(flights, list)
        and body.get("FlightNum") == len(flights)
    )
    if not complete or leg is None:
        return complete
    return (
        str(body.get("FlyOffCityCode", "")).upper() == leg.origin_airport_iata
        and str(body.get("ArriveCityCode", "")).upper() == leg.destination_airport_iata
        and str(body.get("FlyOffTime", ""))[:10] == leg.departure_date.isoformat()
    )


def is_completed_tongcheng_page_state(state: object, leg: LegConfig | None = None) -> bool:
    """Validate Tongcheng's final Nuxt state produced by direct URL navigation."""
    if not isinstance(state, dict):
        return False
    flights = state.get("flightLists")
    complete = state.get("dataflag") == "last" and isinstance(flights, list)
    if not complete or leg is None:
        return complete
    return (
        str(state.get("Departure", "")).upper() == leg.origin_airport_iata
        and str(state.get("Arrival", "")).upper() == leg.destination_airport_iata
        and str(state.get("DepartureDate", ""))[:10] == leg.departure_date.isoformat()
    )


def _decimal(value: Any, field: str) -> Decimal:
    if value is None or value == "":
        raise ParseError(f"缺少 {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ParseError(f"{field} 不是有效数字：{value!r}") from exc
    if result < 0:
        raise ParseError(f"{field} 不能为负数")
    return result


def _datetime(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    raise ParseError(f"{field} 不是可识别的本地日期时间：{value!r}")


def _flight_identity(item: dict[str, Any], etd: datetime, eta: datetime) -> tuple[str, str, str]:
    code = re.sub(r"\s+", "", str(item.get("flightNo") or "").upper())
    if not re.fullmatch(r"[A-Z0-9]{2,3}[0-9]{1,4}[A-Z]?", code):
        raise ParseError(f"无法识别航班号：{item.get('flightNo')!r}")
    carrier = str(item.get("airCompanyCode") or "").strip().upper()
    if not carrier or not code.startswith(carrier):
        carrier = re.match(r"[A-Z0-9]{2,3}(?=[0-9])", code).group()  # type: ignore[union-attr]
    origin = str(item.get("originAirportCode") or "").strip().upper()
    destination = str(item.get("arriveAirportCode") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", origin) or not re.fullmatch(r"[A-Z]{3}", destination):
        raise ParseError("同程响应缺少有效的实际起降机场 IATA")
    canonical = json.dumps(
        [{
            "flight_code": code,
            "origin": origin,
            "destination": destination,
            "etd": etd.isoformat(timespec="minutes"),
            "eta": eta.isoformat(timespec="minutes"),
        }],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return code, carrier, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _duration_minutes(value: Any, etd: datetime, eta: datetime) -> int:
    text = str(value or "")
    hours = re.search(r"(\d+)\s*小时", text)
    minutes = re.search(r"(\d+)\s*分钟", text)
    if hours or minutes:
        return (int(hours.group(1)) if hours else 0) * 60 + (int(minutes.group(1)) if minutes else 0)
    return int((eta - etd).total_seconds() // 60)


def parse_tongcheng_payload(
    payload: dict[str, Any], leg: LegConfig, captured_at: datetime
) -> tuple[list[FlightSnapshot], list[FlightSnapshot | None], int, int]:
    if not is_completed_tongcheng_payload(payload, leg):
        raise IncompleteResponseError("同程响应未通过成功状态、dataflag=all 和航线日期一致性校验")
    body = _body(payload)
    assert body is not None
    candidates = body["FlightInfoSimpleList"]
    eligible: list[FlightSnapshot] = []
    parse_failures: list[str] = []
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            parse_failures.append(f"#{index}: 航班项不是映射")
            continue
        try:
            if int(item.get("stopNum", 0)) != 0:
                continue
            departure_city = str(item.get("departureCityCode") or "").upper()
            arrival_city = str(item.get("arrivalCityCode") or "").upper()
            actual_origin = str(item.get("originAirportCode") or "").upper()
            actual_destination = str(item.get("arriveAirportCode") or "").upper()
            if leg.origin_airport_iata not in {departure_city, actual_origin}:
                continue
            if leg.destination_airport_iata not in {arrival_city, actual_destination}:
                continue
            etd = _datetime(item.get("flyOffTime"), "flyOffTime")
            eta = _datetime(item.get("arrivalTime"), "arrivalTime")
            if eta < etd:
                raise ParseError("arrivalTime 早于 flyOffTime")
            if etd.date() != leg.departure_date or not leg.etd_window.contains(etd.time()):
                continue
            code, carrier, signature = _flight_identity(item, etd, eta)

            # Observed Tongcheng fields: lcp is the adult fare displayed as
            # “¥…起”; pt and ot are the adult airport/fuel taxes. Alerts must
            # compare the tax-inclusive sum rather than lcp alone.
            fare = _decimal(item.get("lcp"), "lcp")
            passenger_tax = _decimal(item.get("pt"), "pt")
            fuel_tax = _decimal(item.get("ot"), "ot")
            tax = passenger_tax + fuel_tax
            seats = item.get("availableTickets")
            remaining = str(seats) if isinstance(seats, int) and seats > 0 else None
            eligible.append(
                FlightSnapshot(
                    flight_signature=signature,
                    flight_codes=(code,),
                    carrier_codes=(carrier,),
                    origin_airport_iata=actual_origin,
                    destination_airport_iata=actual_destination,
                    departure_date=etd.date(),
                    etd_local=etd,
                    eta_local=eta,
                    duration_minutes=_duration_minutes(item.get("spantime"), etd, eta),
                    segment_count=1,
                    is_direct=True,
                    base_price_cny=fare,
                    tax_cny=tax,
                    total_price_cny=fare + tax,
                    currency_code="CNY",
                    remaining_seats=remaining,
                    free_baggage_piece=None,
                    free_baggage_weight=None,
                    source_domain="ly.com",
                    captured_at=captured_at,
                    raw_item=item,
                )
            )
        except (ParseError, TypeError, ValueError) as exc:
            parse_failures.append(f"#{index}: {exc}")

    if not eligible and parse_failures and len(parse_failures) == len(candidates):
        raise ParseError(f"所有 {len(candidates)} 个同程航班均解析失败：{'; '.join(parse_failures[:3])}")
    ranked, preferred_matches, eligible_count = rank_flights(eligible, leg)
    return ranked, preferred_matches, len(candidates), eligible_count


def parse_tongcheng_page_state(
    state: dict[str, Any], leg: LegConfig, captured_at: datetime
) -> tuple[list[FlightSnapshot], list[FlightSnapshot | None], int, int]:
    """Parse the combined server-rendered and appended final page state.

    Direct result URLs preload an initial group and append a ``dataflag=last``
    continuation into ``window.__NUXT__.state.book1.flightLists``. Once the
    state reports ``last``, that array is the page's combined final list.
    """
    if not is_completed_tongcheng_page_state(state, leg):
        raise IncompleteResponseError("同程页面状态未报告 dataflag=last，或航线日期不一致")
    flights = state["flightLists"]
    normalized = {
        "resCode": 0,
        "apiSuccess": True,
        "apiCode": 0,
        "body": {
            "ErrorCode": 0,
            "FlyOffCityCode": leg.origin_airport_iata,
            "ArriveCityCode": leg.destination_airport_iata,
            "FlyOffTime": leg.departure_date.isoformat(),
            "FlightNum": len(flights),
            "paging": {"dataflag": "all"},
            "FlightInfoSimpleList": flights,
        },
    }
    return parse_tongcheng_payload(normalized, leg, captured_at)
