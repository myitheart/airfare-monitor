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

from .models import LegResult, RunReport


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
    PRIMARY KEY (run_id, leg_id, flight_signature),
    FOREIGN KEY (run_id, leg_id) REFERENCES leg_results(run_id, leg_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_responses (
    run_id TEXT NOT NULL,
    leg_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    encoding TEXT NOT NULL DEFAULT 'gzip-json-utf8',
    content BLOB NOT NULL,
    PRIMARY KEY (run_id, leg_id),
    FOREIGN KEY (run_id, leg_id) REFERENCES leg_results(run_id, leg_id) ON DELETE CASCADE
);
"""


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


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
            connection.executescript(SCHEMA)
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
                expected_total_price_cny, status, completed_response,
                observed_count, eligible_count, minimum_total_price_cny,
                previous_min_total_cny, threshold_hit, threshold_confirmed,
                captured_at, error_category, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                leg.id,
                leg.origin_airport_iata,
                leg.destination_airport_iata,
                leg.departure_date.isoformat(),
                leg.etd_window.start.strftime("%H:%M"),
                leg.etd_window.end.strftime("%H:%M"),
                str(leg.expected_total_price_cny),
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
        for rank, flight in enumerate(result.flights, start=1):
            connection.execute(
                """INSERT INTO flight_snapshots (
                    run_id, leg_id, rank_number, flight_signature,
                    flight_codes_json, carrier_codes_json, origin_airport_iata,
                    destination_airport_iata, departure_date, etd_local, eta_local,
                    duration_minutes, segment_count, is_direct, base_price_cny,
                    tax_cny, total_price_cny, currency_code, remaining_seats,
                    free_baggage_piece, free_baggage_weight, source_domain, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                """SELECT leg_id, origin_airport_iata, destination_airport_iata,
                          captured_at, minimum_total_price_cny, status
                   FROM leg_results
                   WHERE captured_at >= ?
                   ORDER BY captured_at, leg_id""",
                (_iso(since),),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_raw_responses(self, keep_days: int, *, now: datetime | None = None) -> int:
        if keep_days < 0:
            raise ValueError("keep_days cannot be negative")
        cutoff = (now or datetime.now()) - timedelta(days=keep_days)
        with closing(self.connect()) as connection:
            with connection:
                cursor = connection.execute("DELETE FROM raw_responses WHERE captured_at < ?", (_iso(cutoff),))
                return cursor.rowcount
