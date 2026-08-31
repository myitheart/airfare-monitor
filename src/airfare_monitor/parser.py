"""Parse completed Qunar international-search payloads into stable snapshots.

The public contract is deliberately isolated here because the observed payload is
not a documented API. Unknown shapes fail closed instead of producing a false
successful run.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .errors import IncompleteResponseError, ParseError
from .models import FlightSnapshot, LegConfig, Segment

_FLIGHT_CODE_RE = re.compile(r"^([A-Z0-9]{2,3})\s*[- ]?\s*([0-9]{1,4}[A-Z]?)$", re.IGNORECASE)


def is_completed_payload(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("result"), dict)
        and isinstance(payload["result"].get("ctrlInfo"), dict)
        and payload["result"]["ctrlInfo"].get("completed") is True
    )


def _first(mapping: dict[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        current: Any = mapping
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None and current != "":
                return current
    return default


def _decimal(value: Any, field: str, *, required: bool = False) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise ParseError(f"缺少 {field}")
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("¥", "").strip()
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ParseError(f"{field} 不是有效数字：{value!r}") from exc
    if parsed < 0:
        raise ParseError(f"{field} 不能为负数")
    return parsed


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def _iata(value: Any, field: str) -> str:
    text = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", text):
        raise ParseError(f"{field} 不是机场 IATA 三字码：{value!r}")
    return text


def _flight_code(value: Any) -> tuple[str, str]:
    text = re.sub(r"\s+", "", str(value or "").upper())
    match = _FLIGHT_CODE_RE.fullmatch(text)
    if not match:
        raise ParseError(f"无法识别航班号：{value!r}")
    carrier, number = match.groups()
    return f"{carrier}{number}", carrier


def _datetime_value(value: Any, field: str, *, fallback_date: date | None = None) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or "").strip()
    if fallback_date and re.fullmatch(r"\d{1,2}:\d{2}", text):
        text = f"{fallback_date.isoformat()} {text}"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y%m%d %H%M", "%Y-%m-%d %H%M"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                pass
        if parsed is None:
            raise ParseError(f"{field} 不是可识别的本地日期时间：{value!r}")
    return parsed.replace(tzinfo=None)


def _combined_datetime(
    mapping: dict[str, Any],
    datetime_paths: tuple[str, ...],
    date_paths: tuple[str, ...],
    time_paths: tuple[str, ...],
    field: str,
    default_date: date,
) -> datetime:
    combined = _first(mapping, *datetime_paths)
    if combined is not None:
        return _datetime_value(combined, field, fallback_date=default_date)
    raw_date = _first(mapping, *date_paths, default=default_date.isoformat())
    raw_time = _first(mapping, *time_paths)
    if raw_time is None:
        raise ParseError(f"缺少 {field}")
    return _datetime_value(f"{raw_date} {raw_time}", field)


def _segment_dicts(item: dict[str, Any]) -> list[dict[str, Any]]:
    trips = _first(item, "journey.trips")
    if isinstance(trips, list):
        segments = [
            segment
            for trip in trips
            if isinstance(trip, dict)
            for segment in trip.get("flightSegments", [])
            if isinstance(segment, dict)
        ]
        if segments:
            return segments
    candidates = (
        "segments",
        "flightSegments",
        "segmentList",
        "flightSegmentList",
        "flightInfoList",
        "flightInfos",
        "flightList",
        "journey.segments",
    )
    raw = _first(item, *candidates)
    if isinstance(raw, dict):
        for key in ("segments", "segmentList", "flightList", "flightInfoList"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        # Some observed shapes store one direct segment on the itinerary itself.
        if _first(item, "flightCode", "flightNo", "flightNumber"):
            return [item]
        raise ParseError("未找到航段列表")
    segments = [entry for entry in raw if isinstance(entry, dict)]
    if not segments:
        raise ParseError("航段列表为空")
    return segments


def _parse_segment(raw: dict[str, Any], leg: LegConfig) -> Segment:
    # Some payloads wrap the actual flight fields one level down.
    wrapped = _first(raw, "flight", "flightInfo", "segment")
    item = {**raw, **wrapped} if isinstance(wrapped, dict) else raw
    code, derived_carrier = _flight_code(
        _first(item, "code", "flightNo", "flightNumber", "marketingFlightNo", "flightCode")
    )
    carrier = str(
        _first(item, "carrierCode", "airlineCode", "marketingCarrierCode", "operatingCarrierCode", default=derived_carrier)
    ).strip().upper()
    origin = _iata(
        _first(item, "originAirportIata", "departureAirportIata", "depAirportCode", "dptAirportCode", "fromAirportCode"),
        "航段出发机场",
    )
    destination = _iata(
        _first(item, "destinationAirportIata", "arrivalAirportIata", "arrAirportCode", "toAirportCode"),
        "航段到达机场",
    )
    etd = _combined_datetime(
        item,
        ("etdLocal", "departureDateTime", "depDateTime", "dptDateTime"),
        ("departureDate", "depDate", "dptDate"),
        ("departureTime", "depTime", "dptTime"),
        "ETD",
        leg.departure_date,
    )
    eta = _combined_datetime(
        item,
        ("etaLocal", "arrivalDateTime", "arrDateTime"),
        ("arrivalDate", "arrDate"),
        ("arrivalTime", "arrTime"),
        "ETA",
        etd.date(),
    )
    if eta < etd:
        # A time-only overnight arrival is normally the following local day.
        from datetime import timedelta

        eta += timedelta(days=1)
    return Segment(code, carrier, origin, destination, etd, eta)


def _looks_like_itinerary(mapping: dict[str, Any]) -> bool:
    price = mapping.get("price")
    return isinstance(price, dict) and price.get("lowTotalPrice") not in (None, "")


def _walk_itineraries(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if _looks_like_itinerary(value):
            yield value
            return
        for child in value.values():
            yield from _walk_itineraries(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_itineraries(child)


def _signature(segments: list[Segment]) -> str:
    identity = [
        {
            "flight_code": segment.flight_code,
            "origin": segment.origin_airport_iata,
            "destination": segment.destination_airport_iata,
            "etd": segment.etd_local.isoformat(timespec="minutes"),
            "eta": segment.eta_local.isoformat(timespec="minutes"),
        }
        for segment in segments
    ]
    canonical = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_completed_payload(
    payload: dict[str, Any], leg: LegConfig, captured_at: datetime
) -> tuple[list[FlightSnapshot], int, int]:
    """Parse, filter and rank one completed response.

    Returns retained flights, observed itinerary count and eligible count.
    """
    if not is_completed_payload(payload):
        raise IncompleteResponseError("响应尚未报告 result.ctrlInfo.completed == true")

    result = payload["result"]
    candidates = list(_walk_itineraries(result))
    if not candidates:
        # An explicitly empty final result is valid only when the payload exposes a known empty list.
        known_list = _first(result, "flights", "flightList", "data.flights", "data.flightList", "flightData")
        if known_list == []:
            return [], 0, 0
        raise ParseError("完整响应中未找到包含 price.lowTotalPrice 的航班组合")

    eligible: list[FlightSnapshot] = []
    parse_failures: list[str] = []
    for index, item in enumerate(candidates, start=1):
        try:
            segments = [_parse_segment(raw, leg) for raw in _segment_dicts(item)]
            search_origin = str(_first(item, "journey.depCityCode", default=segments[0].origin_airport_iata)).upper()
            search_destination = str(
                _first(item, "journey.arrCityCode", default=segments[-1].destination_airport_iata)
            ).upper()
            if search_origin != leg.origin_airport_iata:
                continue
            if search_destination != leg.destination_airport_iata:
                continue
            if segments[0].etd_local.date() != leg.departure_date:
                continue
            if not leg.etd_window.contains(segments[0].etd_local.time()):
                continue
            if leg.direct_only and len(segments) != 1:
                continue

            price = item["price"]
            currency = str(_first(price, "currencyCode", "currency", default="CNY")).upper()
            if currency != "CNY":
                continue
            total = _decimal(price.get("lowTotalPrice"), "price.lowTotalPrice", required=True)
            assert total is not None
            duration = _integer(_first(item, "durationMinutes", "duration", "flightDuration", "journey.duration"))
            if duration is None:
                duration = int((segments[-1].eta_local - segments[0].etd_local).total_seconds() // 60)
            eligible.append(
                FlightSnapshot(
                    flight_signature=_signature(segments),
                    flight_codes=tuple(segment.flight_code for segment in segments),
                    carrier_codes=tuple(dict.fromkeys(segment.carrier_code for segment in segments)),
                    origin_airport_iata=segments[0].origin_airport_iata,
                    destination_airport_iata=segments[-1].destination_airport_iata,
                    departure_date=segments[0].etd_local.date(),
                    etd_local=segments[0].etd_local,
                    eta_local=segments[-1].eta_local,
                    duration_minutes=duration,
                    segment_count=len(segments),
                    is_direct=len(segments) == 1,
                    base_price_cny=_decimal(price.get("lowPrice"), "price.lowPrice"),
                    tax_cny=_decimal(price.get("tax"), "price.tax"),
                    total_price_cny=total,
                    currency_code=currency,
                    remaining_seats=str(
                        _first(
                            item,
                            "remainingSeats",
                            "seatCount",
                            "seatStatus",
                            "price.seatCount",
                            "journey.seatInfo.nums",
                            default="",
                        )
                    )
                    or None,
                    free_baggage_piece=_integer(
                        _first(
                            item,
                            "freeBaggagePiece",
                            "baggage.piece",
                            "baggage.pieceCount",
                            "price.freeCheckLuggagePiece",
                        )
                    ),
                    free_baggage_weight=(
                        str(
                            _first(
                                item,
                                "freeBaggageWeight",
                                "baggage.weight",
                                "price.freeCheckLuggageWeight",
                                default="",
                            )
                        )
                        or None
                    ),
                    source_domain=(
                        str(
                            _first(
                                item,
                                "sourceDomain",
                                "vendorDomain",
                                "otaName",
                                "price.domain",
                                default="",
                            )
                        )
                        or None
                    ),
                    captured_at=captured_at,
                    raw_item=item,
                )
            )
        except ParseError as exc:
            parse_failures.append(f"#{index}: {exc}")

    if not eligible and parse_failures and len(parse_failures) == len(candidates):
        preview = "; ".join(parse_failures[:3])
        raise ParseError(f"所有 {len(candidates)} 个组合均解析失败：{preview}")

    eligible.sort(key=lambda flight: (flight.total_price_cny, flight.etd_local, flight.flight_signature))
    # Qunar may return the same itinerary from multiple suppliers.  The
    # signature intentionally excludes supplier and price, so keep only its
    # cheapest total-price offer before ranking.
    unique: dict[str, FlightSnapshot] = {}
    for flight in eligible:
        unique.setdefault(flight.flight_signature, flight)
    ranked = list(unique.values())
    eligible_count = len(ranked)
    return ranked[: leg.top_n], len(candidates), eligible_count
