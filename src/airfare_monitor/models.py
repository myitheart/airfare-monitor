"""Typed domain models shared by collection, storage and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum


class LegStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    MANUAL_ATTENTION = "manual_attention"


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EtdWindow:
    start: time
    end: time

    def contains(self, value: time) -> bool:
        """Return whether a local time is in the window, including overnight windows."""
        if self.start <= self.end:
            return self.start <= value <= self.end
        return value >= self.start or value <= self.end

    def display(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"


@dataclass(frozen=True, slots=True)
class PreferredSchedule:
    """One exact itinerary schedule that should be called out in reports."""

    label: str
    departure_time: time
    arrival_time: time
    arrival_day_offset: int = 0
    departure_tolerance_minutes: int = 0
    arrival_tolerance_minutes: int = 0
    origin_airport_iata: str | None = None
    destination_airport_iata: str | None = None

    def history_key(self, departure_date: date) -> str:
        """Return a stable identity that resets when the planned trip changes."""
        return "|".join(
            (
                departure_date.isoformat(),
                self.origin_airport_iata or "*",
                self.destination_airport_iata or "*",
                self.departure_time.strftime("%H:%M"),
                self.arrival_time.strftime("%H:%M"),
                str(self.arrival_day_offset),
            )
        )


@dataclass(frozen=True, slots=True)
class LegConfig:
    id: str
    enabled: bool
    origin_airport_iata: str
    destination_airport_iata: str
    departure_date: date
    etd_window: EtdWindow
    direct_only: bool
    expected_total_price_cny: Decimal
    top_n: int
    adult_count: int
    child_count: int
    cabin_class: str
    origin_name_zh: str | None = None
    destination_name_zh: str | None = None
    preferred_schedules: tuple[PreferredSchedule, ...] = ()

    @property
    def route_label(self) -> str:
        return f"{self.origin_airport_iata}-{self.destination_airport_iata}"

    @property
    def origin_display(self) -> str:
        return (
            f"{self.origin_name_zh}（{self.origin_airport_iata}）"
            if self.origin_name_zh
            else self.origin_airport_iata
        )

    @property
    def destination_display(self) -> str:
        return (
            f"{self.destination_name_zh}（{self.destination_airport_iata}）"
            if self.destination_name_zh
            else self.destination_airport_iata
        )

    @property
    def route_display(self) -> str:
        return f"{self.origin_display} → {self.destination_display}"


@dataclass(frozen=True, slots=True)
class Segment:
    flight_code: str
    carrier_code: str
    origin_airport_iata: str
    destination_airport_iata: str
    etd_local: datetime
    eta_local: datetime


@dataclass(frozen=True, slots=True)
class FlightSnapshot:
    flight_signature: str
    flight_codes: tuple[str, ...]
    carrier_codes: tuple[str, ...]
    origin_airport_iata: str
    destination_airport_iata: str
    departure_date: date
    etd_local: datetime
    eta_local: datetime
    duration_minutes: int | None
    segment_count: int
    is_direct: bool
    base_price_cny: Decimal | None
    tax_cny: Decimal | None
    total_price_cny: Decimal
    currency_code: str
    remaining_seats: str | None
    free_baggage_piece: int | None
    free_baggage_weight: str | None
    source_domain: str | None
    captured_at: datetime
    raw_item: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def flight_codes_display(self) -> str:
        return "/".join(self.flight_codes)

    @property
    def carrier_codes_display(self) -> str:
        return "/".join(self.carrier_codes)


@dataclass(frozen=True, slots=True)
class PreferredPriceReference:
    first_total_price_cny: Decimal | None = None
    first_captured_at: datetime | None = None
    previous_total_price_cny: Decimal | None = None
    previous_captured_at: datetime | None = None


@dataclass(slots=True)
class LegResult:
    leg: LegConfig
    status: LegStatus
    captured_at: datetime
    flights: list[FlightSnapshot] = field(default_factory=list)
    preferred_matches: list[FlightSnapshot | None] = field(default_factory=list)
    preferred_price_references: list[PreferredPriceReference] = field(default_factory=list)
    completed_response: bool = False
    observed_count: int = 0
    eligible_count: int = 0
    previous_min_total_cny: Decimal | None = None
    error_category: str | None = None
    error_message: str | None = None
    raw_response: dict | None = field(default=None, repr=False)

    @property
    def minimum_total_cny(self) -> Decimal | None:
        return self.flights[0].total_price_cny if self.flights else None

    @property
    def threshold_hit(self) -> bool:
        value = self.minimum_total_cny
        return self.status == LegStatus.SUCCESS and value is not None and value <= self.leg.expected_total_price_cny


@dataclass(slots=True)
class RunReport:
    run_id: str
    started_at: datetime
    finished_at: datetime
    status: RunStatus
    legs: list[LegResult]
    threshold_confirmed_leg_ids: set[str] = field(default_factory=set)

    @property
    def confirmed_hits(self) -> list[LegResult]:
        return [item for item in self.legs if item.leg.id in self.threshold_confirmed_leg_ids]
