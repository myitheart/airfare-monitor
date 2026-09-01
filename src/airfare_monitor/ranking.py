"""Shared flight de-duplication, ranking and preferred-schedule matching."""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import FlightSnapshot, LegConfig


def rank_flights(
    flights: list[FlightSnapshot], leg: LegConfig
) -> tuple[list[FlightSnapshot], list[FlightSnapshot | None], int]:
    flights.sort(key=lambda flight: (flight.total_price_cny, flight.etd_local, flight.flight_signature))
    unique: dict[str, FlightSnapshot] = {}
    for flight in flights:
        unique.setdefault(flight.flight_signature, flight)
    ranked = list(unique.values())

    preferred_matches: list[FlightSnapshot | None] = []
    for preferred in leg.preferred_schedules:
        arrival_date = leg.departure_date + timedelta(days=preferred.arrival_day_offset)
        target_departure = datetime.combine(leg.departure_date, preferred.departure_time)
        target_arrival = datetime.combine(arrival_date, preferred.arrival_time)
        matches: list[tuple[float, float, FlightSnapshot]] = []
        for flight in ranked:
            if preferred.origin_airport_iata and flight.origin_airport_iata != preferred.origin_airport_iata:
                continue
            if (
                preferred.destination_airport_iata
                and flight.destination_airport_iata != preferred.destination_airport_iata
            ):
                continue
            departure_delta = abs((flight.etd_local - target_departure).total_seconds()) / 60
            arrival_delta = abs((flight.eta_local - target_arrival).total_seconds()) / 60
            if departure_delta > preferred.departure_tolerance_minutes:
                continue
            if arrival_delta > preferred.arrival_tolerance_minutes:
                continue
            matches.append((departure_delta, arrival_delta, flight))
        match = min(
            matches,
            key=lambda item: (
                item[0] + item[1],
                item[0],
                item[1],
                item[2].total_price_cny,
                item[2].flight_signature,
            ),
            default=None,
        )
        preferred_matches.append(match[2] if match is not None else None)
    return ranked[: leg.top_n], preferred_matches, len(ranked)
