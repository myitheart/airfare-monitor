"""SQLite persistence for collection runs and flight snapshots."""

from __future__ import annotations

import gzip
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import LegResult, PreferredPriceReference, RunReport


SCHEMA_VERSION = 1


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'failed')),
    threshold_confirmed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leg_results (
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id) ON DELETE CASCADE,
    leg_id TEXT NOT NULL,
    origin_airport_iata TEXT NOT NULL,
    destination_airport_iata TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    etd_window_start TEXT NOT NULL,
    etd_window_end TEXT NOT NULL,
    return_date TEXT,
    return_etd_window_start TEXT,
    return_etd_window_end TEXT,
    expected_total_price_cny TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'manual_attention')),
    completed_response INTEGER NOT NULL,
    observed_count INTEGER NOT NULL,
    eligible_count INTEGER NOT NULL,
    minimum_total_price_cny TEXT,
    previous_min_total_cny TEXT,
    threshold_hit INTEGER NOT NULL,
    threshold_confirmed INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    error_category TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, leg_id)
);

CREATE INDEX IF NOT EXISTS idx_leg_results_history
ON leg_results (leg_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS flight_snapshots (
    run_id TEXT NOT NULL,
    leg_id TEXT NOT NULL,
    rank_number INTEGER NOT NULL,
    flight_signature TEXT NOT NULL,
    flight_codes_json TEXT NOT NULL,
    carrier_codes_json TEXT NOT NULL,
    origin_airport_iata TEXT NOT NULL,
    destination_airport_iata TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    etd_local TEXT NOT NULL,
    eta_local TEXT NOT NULL,
    duration_minutes INTEGER,
    segment_count INTEGER NOT NULL,
    is_direct INTEGER NOT NULL,
    base_price_cny TEXT,
    tax_cny TEXT,
    total_price_cny TEXT NOT NULL,
    currency_code TEXT NOT NULL,
    remaining_seats TEXT,
    free_baggage_piece INTEGER,
    free_baggage_weight TEXT,
    source_domain TEXT,
    captured_at TEXT NOT NULL,
    connection_airports_json TEXT NOT NULL DEFAULT '[]',
    layover_minutes INTEGER,
    return_itinerary_json TEXT,
    seat_availability_json TEXT,
    outbound_seat_availability_json TEXT,
    PRIMARY KEY (run_id, leg_id, flight_signature),
    FOREIGN KEY (run_id, leg_id) REFERENCES leg_results(run_id, leg_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS preferred_schedule_prices (
    run_id TEXT NOT NULL,
    leg_id TEXT NOT NULL,
    preference_key TEXT NOT NULL,
    label TEXT NOT NULL,
    target_departure_time TEXT NOT NULL,
    target_arrival_time TEXT NOT NULL,
    arrival_day_offset INTEGER NOT NULL,
    matched_flight_signature TEXT,
    matched_flight_codes_json TEXT,
    actual_etd_local TEXT,
    actual_eta_local TEXT,
    total_price_cny TEXT,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (run_id, leg_id, preference_key),
    FOREIGN KEY (run_id, leg_id) REFERENCES leg_results(run_id, leg_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_preferred_schedule_prices_history
ON preferred_schedule_prices (leg_id, preference_key, captured_at);

CREATE TABLE IF NOT EXISTS raw_responses (
    run_id TEXT NOT NULL,
    leg_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    encoding TEXT NOT NULL DEFAULT 'gzip-json-utf8',
    content BLOB NOT NULL,
    PRIMARY KEY (run_id, leg_id),
    FOREIGN KEY (run_id, leg_id) REFERENCES leg_results(run_id, leg_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    leg_id TEXT,
    message TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_events_recent
ON app_events (occurred_at DESC);
"""


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _seat_availability_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "count_hint": value.count_hint,
        "count_text": value.count_text,
        "scarcity_text": value.scarcity_text,
        "ticket_insufficient": value.ticket_insufficient,
    }


def _seat_availability_json(value: Any) -> str | None:
    mapping = _seat_availability_mapping(value)
    if mapping is None:
        return None
    return json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))


def _return_itinerary_json(result: Any) -> str | None:
    itinerary = result.return_itinerary
    if itinerary is None:
        return None
    return json.dumps(
        {
            "flight_signature": itinerary.flight_signature,
            "flight_codes": itinerary.flight_codes,
            "carrier_codes": itinerary.carrier_codes,
            "origin_airport_iata": itinerary.origin_airport_iata,
            "destination_airport_iata": itinerary.destination_airport_iata,
            "departure_date": itinerary.departure_date.isoformat(),
            "etd_local": _iso(itinerary.etd_local),
            "eta_local": _iso(itinerary.eta_local),
            "duration_minutes": itinerary.duration_minutes,
            "segment_count": itinerary.segment_count,
            "is_direct": itinerary.is_direct,
            "connection_airports": itinerary.connection_airports,
            "layover_minutes": itinerary.layover_minutes,
            "seat_availability": _seat_availability_mapping(itinerary.seat_availability),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _decode_flight_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    """Decode JSON columns into presentation-ready values without raw response data."""

    value = dict(row)
    value["flight_codes"] = _load_json(value.pop("flight_codes_json"), [])
    value["carrier_codes"] = _load_json(value.pop("carrier_codes_json"), [])
    value["connection_airports"] = _load_json(value.pop("connection_airports_json"), [])
    value["return_itinerary"] = _load_json(value.pop("return_itinerary_json"), None)
    value["seat_availability"] = _load_json(value.pop("seat_availability_json"), None)
    value["outbound_seat_availability"] = _load_json(
        value.pop("outbound_seat_availability_json"), None
    )
    return value


def _load_json(value: object, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                raise RuntimeError("价格数据库由更新版本创建；请使用较新的航价守望程序打开")
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(flight_snapshots)")}
            if "connection_airports_json" not in columns:
                connection.execute(
                    "ALTER TABLE flight_snapshots ADD COLUMN connection_airports_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "layover_minutes" not in columns:
                connection.execute("ALTER TABLE flight_snapshots ADD COLUMN layover_minutes INTEGER")
            if "return_itinerary_json" not in columns:
                connection.execute("ALTER TABLE flight_snapshots ADD COLUMN return_itinerary_json TEXT")
            if "seat_availability_json" not in columns:
                connection.execute("ALTER TABLE flight_snapshots ADD COLUMN seat_availability_json TEXT")
            if "outbound_seat_availability_json" not in columns:
                connection.execute(
                    "ALTER TABLE flight_snapshots ADD COLUMN outbound_seat_availability_json TEXT"
                )
            if "luggage_inclusive_price_cny" not in columns:
                connection.execute(
                    "ALTER TABLE flight_snapshots ADD COLUMN luggage_inclusive_price_cny TEXT"
                )
            leg_columns = {row[1] for row in connection.execute("PRAGMA table_info(leg_results)")}
            for name in ("return_date", "return_etd_window_start", "return_etd_window_end"):
                if name not in leg_columns:
                    connection.execute(f"ALTER TABLE leg_results ADD COLUMN {name} TEXT")
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()

    def previous_minimum(self, leg_id: str, *, before: datetime | None = None) -> Decimal | None:
        sql = """
            SELECT minimum_total_price_cny
            FROM leg_results
            WHERE leg_id = ? AND status = 'success' AND minimum_total_price_cny IS NOT NULL
        """
        params: list[Any] = [leg_id]
        if before is not None:
            sql += " AND captured_at < ?"
            params.append(_iso(before))
        sql += " ORDER BY captured_at DESC LIMIT 1"
        with closing(self.connect()) as connection:
            row = connection.execute(sql, params).fetchone()
        return Decimal(row[0]) if row else None

    def preferred_price_reference(
        self, leg_id: str, preference_key: str, *, before: datetime | None = None
    ) -> PreferredPriceReference:
        sql = """
            SELECT total_price_cny, captured_at
            FROM preferred_schedule_prices
            WHERE leg_id = ? AND preference_key = ? AND total_price_cny IS NOT NULL
        """
        params: list[Any] = [leg_id, preference_key]
        if before is not None:
            sql += " AND captured_at < ?"
            params.append(_iso(before))
        with closing(self.connect()) as connection:
            first = connection.execute(sql + " ORDER BY captured_at ASC, run_id ASC LIMIT 1", params).fetchone()
            previous = connection.execute(sql + " ORDER BY captured_at DESC, run_id DESC LIMIT 1", params).fetchone()
        return PreferredPriceReference(
            first_total_price_cny=Decimal(first[0]) if first else None,
            first_captured_at=datetime.fromisoformat(first[1]) if first else None,
            previous_total_price_cny=Decimal(previous[0]) if previous else None,
            previous_captured_at=datetime.fromisoformat(previous[1]) if previous else None,
        )

    def flight_price_reference(
        self, leg_id: str, flight_signature: str, *, before: datetime | None = None
    ) -> PreferredPriceReference:
        """Return the first and latest prior price for the same complete itinerary."""
        sql = """
            SELECT total_price_cny, captured_at
            FROM flight_snapshots
            WHERE leg_id = ? AND flight_signature = ?
        """
        params: list[Any] = [leg_id, flight_signature]
        if before is not None:
            sql += " AND captured_at < ?"
            params.append(_iso(before))
        with closing(self.connect()) as connection:
            first = connection.execute(
                sql + " ORDER BY captured_at ASC, run_id ASC LIMIT 1", params
            ).fetchone()
            previous = connection.execute(
                sql + " ORDER BY captured_at DESC, run_id DESC LIMIT 1", params
            ).fetchone()
        return PreferredPriceReference(
            first_total_price_cny=Decimal(first[0]) if first else None,
            first_captured_at=datetime.fromisoformat(first[1]) if first else None,
            previous_total_price_cny=Decimal(previous[0]) if previous else None,
            previous_captured_at=datetime.fromisoformat(previous[1]) if previous else None,
        )

    def save_report(self, report: RunReport) -> None:
        confirmed_ids = report.threshold_confirmed_leg_ids
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    """INSERT INTO collection_runs
                       (run_id, started_at, finished_at, status, threshold_confirmed_count)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        report.run_id,
                        _iso(report.started_at),
                        _iso(report.finished_at),
                        str(report.status),
                        len(confirmed_ids),
                    ),
                )
                for result in report.legs:
                    self._insert_leg(connection, report.run_id, result, result.leg.id in confirmed_ids)

    def _insert_leg(
        self, connection: sqlite3.Connection, run_id: str, result: LegResult, threshold_confirmed: bool
    ) -> None:
        leg = result.leg
        connection.execute(
            """INSERT INTO leg_results (
                run_id, leg_id, origin_airport_iata, destination_airport_iata,
                departure_date, etd_window_start, etd_window_end,
                return_date, return_etd_window_start, return_etd_window_end,
                expected_total_price_cny, status, completed_response,
                observed_count, eligible_count, minimum_total_price_cny,
                previous_min_total_cny, threshold_hit, threshold_confirmed,
                captured_at, error_category, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                leg.id,
                leg.origin_airport_iata,
                leg.destination_airport_iata,
                leg.departure_date.isoformat(),
                leg.etd_window.start.strftime("%H:%M"),
                leg.etd_window.end.strftime("%H:%M"),
                leg.return_date.isoformat() if leg.return_date else None,
                leg.return_etd_window.start.strftime("%H:%M") if leg.return_etd_window else None,
                leg.return_etd_window.end.strftime("%H:%M") if leg.return_etd_window else None,
                _decimal_text(leg.expected_total_price_cny) or "",
                str(result.status),
                int(result.completed_response),
                result.observed_count,
                result.eligible_count,
                _decimal_text(result.minimum_total_cny),
                _decimal_text(result.previous_min_total_cny),
                int(result.threshold_hit),
                int(threshold_confirmed),
                _iso(result.captured_at),
                result.error_category,
                result.error_message,
            ),
        )
        stored_candidates = result.candidate_flights or result.flights
        for rank, flight in enumerate(stored_candidates, start=1):
            connection.execute(
                """INSERT INTO flight_snapshots (
                    run_id, leg_id, rank_number, flight_signature,
                    flight_codes_json, carrier_codes_json, origin_airport_iata,
                    destination_airport_iata, departure_date, etd_local, eta_local,
                    duration_minutes, segment_count, is_direct, base_price_cny,
                    tax_cny, total_price_cny, currency_code, remaining_seats,
                    free_baggage_piece, free_baggage_weight, source_domain, captured_at,
                    connection_airports_json, layover_minutes, return_itinerary_json,
                    seat_availability_json, outbound_seat_availability_json,
                    luggage_inclusive_price_cny
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    leg.id,
                    rank,
                    flight.flight_signature,
                    json.dumps(flight.flight_codes, ensure_ascii=False),
                    json.dumps(flight.carrier_codes, ensure_ascii=False),
                    flight.origin_airport_iata,
                    flight.destination_airport_iata,
                    flight.departure_date.isoformat(),
                    _iso(flight.etd_local),
                    _iso(flight.eta_local),
                    flight.duration_minutes,
                    flight.segment_count,
                    int(flight.is_direct),
                    _decimal_text(flight.base_price_cny),
                    _decimal_text(flight.tax_cny),
                    str(flight.total_price_cny),
                    flight.currency_code,
                    flight.remaining_seats,
                    flight.free_baggage_piece,
                    flight.free_baggage_weight,
                    flight.source_domain,
                    _iso(flight.captured_at),
                    json.dumps(flight.connection_airports, ensure_ascii=False),
                    flight.layover_minutes,
                    _return_itinerary_json(flight),
                    _seat_availability_json(flight.seat_availability),
                    _seat_availability_json(flight.outbound_seat_availability),
                    _decimal_text(flight.luggage_inclusive_price_cny),
                ),
            )
        for index, preferred in enumerate(leg.preferred_schedules):
            flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
            connection.execute(
                """INSERT INTO preferred_schedule_prices (
                    run_id, leg_id, preference_key, label,
                    target_departure_time, target_arrival_time, arrival_day_offset,
                    matched_flight_signature, matched_flight_codes_json,
                    actual_etd_local, actual_eta_local, total_price_cny, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    leg.id,
                    preferred.history_key(leg.departure_date),
                    preferred.label,
                    preferred.departure_time.strftime("%H:%M"),
                    preferred.arrival_time.strftime("%H:%M"),
                    preferred.arrival_day_offset,
                    flight.flight_signature if flight else None,
                    json.dumps(flight.flight_codes, ensure_ascii=False) if flight else None,
                    _iso(flight.etd_local) if flight else None,
                    _iso(flight.eta_local) if flight else None,
                    _decimal_text(flight.total_price_cny) if flight else None,
                    _iso(result.captured_at),
                ),
            )
        if result.raw_response is not None:
            raw = json.dumps(result.raw_response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            connection.execute(
                "INSERT INTO raw_responses (run_id, leg_id, captured_at, content) VALUES (?, ?, ?, ?)",
                (run_id, leg.id, _iso(result.captured_at), gzip.compress(raw)),
            )

    def history(self, *, since: datetime) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT leg_id, origin_airport_iata, destination_airport_iata, return_date,
                          captured_at, minimum_total_price_cny, status
                   FROM leg_results
                   WHERE captured_at >= ?
                   ORDER BY captured_at, leg_id""",
                (_iso(since),),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_run(self) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT run_id, started_at, finished_at, status, threshold_confirmed_count "
                "FROM collection_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def recent_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT run_id, started_at, finished_at, status, threshold_confirmed_count "
                "FROM collection_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_successful_run(self) -> dict[str, Any] | None:
        """Return the newest run that produced at least one valid leg result."""
        with closing(self.connect()) as connection:
            row = connection.execute(
                """SELECT runs.run_id, runs.started_at, runs.finished_at, runs.status,
                          runs.threshold_confirmed_count
                   FROM collection_runs runs
                   WHERE EXISTS (
                       SELECT 1 FROM leg_results legs
                       WHERE legs.run_id = runs.run_id AND legs.status = 'success'
                   )
                   ORDER BY runs.finished_at DESC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def latest_leg_results(self, leg_ids: list[str]) -> list[dict[str, Any]]:
        if not leg_ids:
            return []
        placeholders = ",".join("?" for _ in leg_ids)
        sql = f"""
            SELECT current.* FROM leg_results current
            JOIN (
                SELECT leg_id, MAX(captured_at) AS captured_at
                FROM leg_results WHERE leg_id IN ({placeholders}) GROUP BY leg_id
            ) latest ON latest.leg_id = current.leg_id AND latest.captured_at = current.captured_at
            ORDER BY current.leg_id
        """
        with closing(self.connect()) as connection:
            rows = connection.execute(sql, leg_ids).fetchall()
        return [dict(row) for row in rows]

    def leg_price_series(self, leg_id: str, *, since: datetime, max_points: int = 500) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """SELECT captured_at, minimum_total_price_cny, status
                   FROM leg_results WHERE leg_id = ? AND captured_at >= ?
                   ORDER BY captured_at ASC LIMIT ?""",
                (leg_id, _iso(since), max_points),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_app_event(
        self, *, event_type: str, severity: str, message: str, leg_id: str | None = None, details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        with closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO app_events (occurred_at, event_type, severity, leg_id, message, details_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        _iso(occurred_at or datetime.now()), event_type, severity, leg_id, message,
                        json.dumps(details, ensure_ascii=False, separators=(",", ":")) if details else None,
                    ),
                )

    def recent_app_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT occurred_at, event_type, severity, leg_id, message FROM app_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_flight_candidates(self, leg_id: str) -> dict[str, Any] | None:
        """Return the latest complete successful result and its stored candidates."""

        with closing(self.connect()) as connection:
            result = connection.execute(
                """SELECT * FROM leg_results
                   WHERE leg_id = ? AND status = 'success' AND completed_response = 1
                   ORDER BY captured_at DESC, run_id DESC LIMIT 1""",
                (leg_id,),
            ).fetchone()
            if result is None:
                return None
            flights = connection.execute(
                """SELECT * FROM flight_snapshots
                   WHERE run_id = ? AND leg_id = ?
                   ORDER BY rank_number ASC""",
                (result["run_id"], leg_id),
            ).fetchall()
        payload = dict(result)
        payload["flights"] = [_decode_flight_snapshot(row) for row in flights]
        payload["stored_count"] = len(flights)
        return payload

    def prune_app_events(self, keep_days: int = 30, *, now: datetime | None = None) -> int:
        if keep_days < 0:
            raise ValueError("keep_days cannot be negative")
        cutoff = (now or datetime.now()) - timedelta(days=keep_days)
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM app_events WHERE occurred_at < ?", (_iso(cutoff),)
                )
                return cursor.rowcount

    def prune_raw_responses(self, keep_days: int, *, now: datetime | None = None) -> int:
        if keep_days < 0:
            raise ValueError("keep_days cannot be negative")
        cutoff = (now or datetime.now()) - timedelta(days=keep_days)
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute("DELETE FROM raw_responses WHERE captured_at < ?", (_iso(cutoff),))
                return cursor.rowcount

    def prune_candidate_details(self, keep_days: int = 7, *, now: datetime | None = None) -> int:
        """Expire expanded candidate rows while retaining the historical Top 10."""

        if keep_days < 0:
            raise ValueError("keep_days cannot be negative")
        cutoff = (now or datetime.now()) - timedelta(days=keep_days)
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM flight_snapshots WHERE rank_number > 10 AND captured_at < ?",
                    (_iso(cutoff),),
                )
                return cursor.rowcount
