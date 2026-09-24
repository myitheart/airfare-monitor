"""Read-only presentation models built from SQLite and route configuration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..models import LegConfig
from ..storage import SQLiteStore


@dataclass(frozen=True, slots=True)
class RouteOverview:
    leg_id: str
    status: str
    minimum_total_cny: Decimal | None
    previous_total_cny: Decimal | None
    captured_at: datetime | None
    threshold_confirmed: bool

    @property
    def change_cny(self) -> Decimal | None:
        if self.minimum_total_cny is None or self.previous_total_cny is None:
            return None
        return self.minimum_total_cny - self.previous_total_cny


@dataclass(frozen=True, slots=True)
class DashboardData:
    today_minimum_cny: Decimal | None
    today_minimum_leg_id: str | None
    today_minimum_captured_at: datetime | None
    latest_success_at: datetime | None
    attention_count: int
    latest_run_duration_seconds: int | None
    routes: dict[str, RouteOverview]
    recent_events: tuple[dict[str, object], ...]


def load_dashboard_data(
    store: SQLiteStore,
    routes: list[LegConfig],
    *,
    now: datetime | None = None,
) -> DashboardData:
    current_time = now or datetime.now()
    route_ids = [route.id for route in routes]
    latest_rows = store.latest_leg_results(route_ids)
    overviews: dict[str, RouteOverview] = {}
    for row in latest_rows:
        leg_id = str(row["leg_id"])
        overviews[leg_id] = RouteOverview(
            leg_id=leg_id,
            status=str(row.get("status", "failed")),
            minimum_total_cny=_decimal(row.get("minimum_total_price_cny")),
            previous_total_cny=_decimal(row.get("previous_min_total_cny")),
            captured_at=_datetime(row.get("captured_at")),
            threshold_confirmed=bool(row.get("threshold_confirmed", False)),
        )

    enabled_ids = {route.id for route in routes if route.enabled}
    midnight = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    today_observations = [
        (price, str(row.get("leg_id")), _datetime(row.get("captured_at")))
        for row in store.history(since=midnight)
        if str(row.get("leg_id")) in enabled_ids
        and str(row.get("status")) == "success"
        and (price := _decimal(row.get("minimum_total_price_cny"))) is not None
    ]
    today_minimum = (
        min(today_observations, key=lambda observation: observation[0])
        if today_observations else None
    )
    successful = store.latest_successful_run()
    latest_run = store.latest_run()
    duration: int | None = None
    if latest_run:
        started = _datetime(latest_run.get("started_at"))
        finished = _datetime(latest_run.get("finished_at"))
        if started and finished:
            duration = max(0, int((finished - started).total_seconds()))

    return DashboardData(
        today_minimum_cny=today_minimum[0] if today_minimum else None,
        today_minimum_leg_id=today_minimum[1] if today_minimum else None,
        today_minimum_captured_at=today_minimum[2] if today_minimum else None,
        latest_success_at=_datetime(successful.get("finished_at")) if successful else None,
        attention_count=sum(
            item.status == "manual_attention" and item.leg_id in enabled_ids
            for item in overviews.values()
        ),
        latest_run_duration_seconds=duration,
        routes=overviews,
        recent_events=tuple(store.recent_app_events(limit=12)),
    )


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
