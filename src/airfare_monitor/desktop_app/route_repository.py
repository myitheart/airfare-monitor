"""Desktop route persistence without exposing YAML to end users."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ..config import load_routes, validate_enabled_leg_limit
from ..models import LegConfig
from .yaml_files import atomic_write_yaml


class RouteRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[LegConfig]:
        return load_routes(self.path, allow_empty=True)

    def save(self, legs: list[LegConfig]) -> None:
        validate_enabled_leg_limit(legs)
        payload = {"legs": [_serialize_leg(leg) for leg in legs]}
        atomic_write_yaml(self.path, payload, validate=lambda temporary: load_routes(temporary, allow_empty=True))


def _serialize_leg(leg: LegConfig) -> dict[str, object]:
    record: dict[str, object] = {
        "id": leg.id,
        "enabled": leg.enabled,
        "origin_airport_iata": leg.origin_airport_iata,
        "origin_name_zh": leg.origin_name_zh,
        "destination_airport_iata": leg.destination_airport_iata,
        "destination_name_zh": leg.destination_name_zh,
        "departure_date": leg.departure_date.isoformat(),
        "etd_window": {"start": leg.etd_window.start.strftime("%H:%M"), "end": leg.etd_window.end.strftime("%H:%M")},
        "direct_only": leg.direct_only,
        "expected_total_price_cny": _decimal(leg.expected_total_price_cny),
        "top_n": leg.top_n,
        "adult_count": leg.adult_count,
        "child_count": leg.child_count,
        "cabin_class": leg.cabin_class,
        "market": leg.market,
        "preferred_schedules": [
            {
                "label": item.label,
                "departure_time": item.departure_time.strftime("%H:%M"),
                "arrival_time": item.arrival_time.strftime("%H:%M"),
                "arrival_day_offset": item.arrival_day_offset,
                "departure_tolerance_minutes": item.departure_tolerance_minutes,
                "arrival_tolerance_minutes": item.arrival_tolerance_minutes,
                "origin_airport_iata": item.origin_airport_iata,
                "destination_airport_iata": item.destination_airport_iata,
            }
            for item in leg.preferred_schedules
        ],
    }
    if leg.max_layover_minutes is not None:
        record["max_layover_minutes"] = leg.max_layover_minutes
    if leg.origin_airports is not None:
        record["origin_airports"] = list(leg.origin_airports)
    if leg.destination_airports is not None:
        record["destination_airports"] = list(leg.destination_airports)
    if leg.return_date is not None:
        record["return_date"] = leg.return_date.isoformat()
        assert leg.return_etd_window is not None
        record["return_etd_window"] = {
            "start": leg.return_etd_window.start.strftime("%H:%M"),
            "end": leg.return_etd_window.end.strftime("%H:%M"),
        }
        record["return_direct_only"] = leg.return_direct_only
        if leg.return_max_layover_minutes is not None:
            record["return_max_layover_minutes"] = leg.return_max_layover_minutes
    return record


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
