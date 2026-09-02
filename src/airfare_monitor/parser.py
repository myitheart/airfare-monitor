"""Parse completed Qunar international-search payloads into stable snapshots.

The public contract is deliberately isolated here because the observed payload is
not a documented API. Unknown shapes fail closed instead of producing a false
successful run.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .errors import IncompleteResponseError, ParseError
from .models import FlightSnapshot, ItinerarySnapshot, LegConfig, SeatAvailability, Segment
from .ranking import rank_flights

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


def _seat_availability(item: dict[str, Any]) -> SeatAvailability | None:
    """Extract Qunar's inventory hints without treating them as guaranteed stock."""
    count = _integer(
        _first(
            item,
            "seatInfo.nums",
            "remainingSeats",
            "seatCount",
            "seatStatus",
            "price.seatCount",
            "journey.seatInfo.nums",
        )
    )
    count_text_value = _first(item, "seatInfo.showOTxt", "journey.seatInfo.showOTxt")
    scarcity_value = _first(
        item,
        "seatInfo.showLTxt",
        "seatInfo.otaText",
        "seatInfo.listText",
        "journey.seatInfo.showLTxt",
        "journey.seatInfo.otaText",
        "journey.seatInfo.listText",
    )
    insufficient_value = _first(item, "ticketInsufficient", "journey.ticketInsufficient")
    insufficient = insufficient_value if isinstance(insufficient_value, bool) else None
    if insufficient is None:
        raw_segments: list[dict[str, Any]] = []
        direct_segments = item.get("flightSegments")
        if isinstance(direct_segments, list):
            raw_segments.extend(segment for segment in direct_segments if isinstance(segment, dict))
        journeys = _first(item, "journey.trips")
        if isinstance(journeys, list):
            raw_segments.extend(
                segment
                for trip in journeys
                if isinstance(trip, dict)
                for segment in trip.get("flightSegments", [])
                if isinstance(segment, dict)
            )
        flags = [segment.get("ticketInsufficient") for segment in raw_segments]
        if any(flag is True for flag in flags):
            insufficient = True
        elif any(flag is False for flag in flags):
            insufficient = False
    count_text = str(count_text_value).strip() if count_text_value not in (None, "") else None
    scarcity_text = str(scarcity_value).strip() if scarcity_value not in (None, "") else None
    if count is None and count_text is None and scarcity_text is None and insufficient is None:
        return None
    return SeatAvailability(
        count_hint=count,
        count_text=count_text,
        scarcity_text=scarcity_text,
        ticket_insufficient=insufficient,
    )


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


def _return_leg_config(leg: LegConfig) -> LegConfig:
    if leg.return_date is None or leg.return_etd_window is None:
        raise ParseError("往返配置缺少返程日期或 ETD 时间窗")
    return replace(
        leg,
        origin_airport_iata=leg.destination_airport_iata,
        destination_airport_iata=leg.origin_airport_iata,
        departure_date=leg.return_date,
        etd_window=leg.return_etd_window,
        direct_only=leg.return_direct_only if leg.return_direct_only is not None else leg.direct_only,
        max_layover_minutes=leg.return_max_layover_minutes,
        preferred_schedules=(),
        return_date=None,
        return_etd_window=None,
        return_direct_only=None,
        return_max_layover_minutes=None,
    )


def _eligible_itinerary(
    item: dict[str, Any], leg: LegConfig
) -> tuple[list[Segment], int | None, int | None, SeatAvailability | None] | None:
    segments = [_parse_segment(raw, leg) for raw in _segment_dicts(item)]
    search_origin = str(
        _first(item, "depCityCode", "journey.depCityCode", default=segments[0].origin_airport_iata)
    ).upper()
    search_destination = str(
        _first(item, "arrCityCode", "journey.arrCityCode", default=segments[-1].destination_airport_iata)
    ).upper()
    if search_origin != leg.origin_airport_iata or search_destination != leg.destination_airport_iata:
        return None
    if segments[0].etd_local.date() != leg.departure_date:
        return None
    if not leg.etd_window.contains(segments[0].etd_local.time()):
        return None
    if leg.direct_only and len(segments) != 1:
        return None

    layovers: list[int] = []
    for current, following in zip(segments, segments[1:]):
        if current.destination_airport_iata != following.origin_airport_iata:
            raise ParseError(
                f"相邻航段机场不连续：{current.destination_airport_iata} != {following.origin_airport_iata}"
            )
        layover = int((following.etd_local - current.eta_local).total_seconds() // 60)
        if layover < 0:
            raise ParseError("中转航段时间重叠或顺序错误")
        layovers.append(layover)
    total_layover = sum(layovers) if layovers else None
    if (
        leg.max_layover_minutes is not None
        and total_layover is not None
        and total_layover > leg.max_layover_minutes
    ):
        return None

    duration = _integer(_first(item, "durationMinutes", "duration", "flightDuration", "journey.duration"))
    if duration is None:
        duration = int((segments[-1].eta_local - segments[0].etd_local).total_seconds() // 60)
    return segments, total_layover, duration, _seat_availability(item)


def _itinerary_snapshot(
    segments: list[Segment],
    layover_minutes: int | None,
    duration_minutes: int | None,
    seat_availability: SeatAvailability | None,
) -> ItinerarySnapshot:
    return ItinerarySnapshot(
        flight_signature=_signature(segments),
        flight_codes=tuple(segment.flight_code for segment in segments),
        carrier_codes=tuple(dict.fromkeys(segment.carrier_code for segment in segments)),
        origin_airport_iata=segments[0].origin_airport_iata,
        destination_airport_iata=segments[-1].destination_airport_iata,
        departure_date=segments[0].etd_local.date(),
        etd_local=segments[0].etd_local,
        eta_local=segments[-1].eta_local,
        duration_minutes=duration_minutes,
        segment_count=len(segments),
        is_direct=len(segments) == 1,
        connection_airports=tuple(segment.destination_airport_iata for segment in segments[:-1]),
        layover_minutes=layover_minutes,
        seat_availability=seat_availability,
    )


def parse_completed_payload(
    payload: dict[str, Any], leg: LegConfig, captured_at: datetime
) -> tuple[list[FlightSnapshot], list[FlightSnapshot | None], int, int]:
    """Parse, filter and rank one completed response.

    Returns top-ranked flights, preferred-schedule matches, observed itinerary
    count and eligible count.
    """
    if not is_completed_payload(payload):
        raise IncompleteResponseError("响应尚未报告 result.ctrlInfo.completed == true")

    result = payload["result"]
    flight_prices = result.get("flightPrices")
    if isinstance(flight_prices, dict):
        candidates = [item for item in flight_prices.values() if isinstance(item, dict) and _looks_like_itinerary(item)]
    else:
        candidates = list(_walk_itineraries(result))
    if not candidates:
        # An explicitly empty final result is valid only when the payload exposes a known empty list.
        if isinstance(flight_prices, dict) and not flight_prices:
            return [], [None] * len(leg.preferred_schedules), 0, 0
        known_list = _first(result, "flights", "flightList", "data.flights", "data.flightList", "flightData")
        if known_list == []:
            return [], [None] * len(leg.preferred_schedules), 0, 0
        raise ParseError("完整响应中未找到包含 price.lowTotalPrice 的航班组合")

    eligible: list[FlightSnapshot] = []
    parse_failures: list[str] = []
    return_leg = _return_leg_config(leg) if leg.is_round_trip else None
    for index, item in enumerate(candidates, start=1):
        try:
            outbound = _eligible_itinerary(item, leg) if not leg.is_round_trip else None
            return_itinerary: ItinerarySnapshot | None = None
            inbound_segments_for_signature: list[Segment] = []
            if leg.is_round_trip:
                trips = _first(item, "journey.trips")
                if not isinstance(trips, list) or len(trips) != 2 or not all(isinstance(trip, dict) for trip in trips):
                    raise ParseError("往返报价的 journey.trips 必须恰好包含去程和返程")
                assert return_leg is not None
                outbound = _eligible_itinerary(trips[0], leg)
                inbound = _eligible_itinerary(trips[1], return_leg)
                if outbound is None or inbound is None:
                    continue
                inbound_segments, inbound_layover, inbound_duration, inbound_seats = inbound
                inbound_segments_for_signature = inbound_segments
                return_itinerary = _itinerary_snapshot(
                    inbound_segments, inbound_layover, inbound_duration, inbound_seats
                )
            if outbound is None:
                continue
            segments, total_layover, duration, outbound_seats = outbound

            price = item["price"]
            currency = str(_first(price, "currencyCode", "currency", default="CNY")).upper()
            if currency != "CNY":
                continue
            total = _decimal(price.get("lowTotalPrice"), "price.lowTotalPrice", required=True)
            assert total is not None
            signature_segments = list(segments)
            signature_segments.extend(inbound_segments_for_signature)
            overall_seats = _seat_availability(item)
            eligible.append(
                FlightSnapshot(
                    flight_signature=_signature(signature_segments),
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
                    remaining_seats=(
                        str(overall_seats.count_hint)
                        if overall_seats and overall_seats.count_hint is not None
                        else overall_seats.count_text if overall_seats else None
                    ),
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
                    connection_airports=tuple(segment.destination_airport_iata for segment in segments[:-1]),
                    layover_minutes=total_layover,
                    return_itinerary=return_itinerary,
                    seat_availability=overall_seats,
                    outbound_seat_availability=outbound_seats,
                    raw_item=item,
                )
            )
        except ParseError as exc:
            parse_failures.append(f"#{index}: {exc}")

    if not eligible and parse_failures and len(parse_failures) == len(candidates):
        preview = "; ".join(parse_failures[:3])
        raise ParseError(f"所有 {len(candidates)} 个组合均解析失败：{preview}")

    ranked, preferred_matches, eligible_count = rank_flights(eligible, leg)
    return ranked, preferred_matches, len(candidates), eligible_count
