# Data model

## Route configuration

| Field | Required | Meaning |
|---|---:|---|
| `id` | yes | Stable leg identifier such as `leg-1` |
| `origin_airport_iata` | yes | Origin airport IATA three-letter code |
| `destination_airport_iata` | yes | Destination airport IATA three-letter code |
| `origin_name_zh` | no | Chinese display name used in email and workbook |
| `destination_name_zh` | no | Chinese display name used in email and workbook |
| `departure_date` | yes | Local departure date |
| `etd_window.start/end` | yes | Allowed local scheduled departure-time window |
| `direct_only` | yes | Whether connecting itineraries are excluded |
| `max_layover_minutes` | no | Maximum summed waiting time between adjacent segments |
| `return_date` | no | Enables round-trip combination monitoring and sets the return departure date |
| `return_etd_window.start/end` | round trip | Allowed return departure-time window |
| `return_direct_only` | no | Return-direction direct-flight filter; defaults to outbound setting |
| `return_max_layover_minutes` | no | Return-direction summed layover limit; defaults to outbound setting |
| `expected_total_price_cny` | yes | Psychological total-price threshold; explicit null disables alerts for this leg |
| `top_n` | yes | Number of cheapest eligible flights retained |
| `adult_count` | yes | Adult passenger count used for pricing |
| `child_count` | yes | Child passenger count used for pricing |
| `cabin_class` | yes | Requested cabin class |
| `preferred_schedules` | no | Exact schedules shown separately with live prices in each email |

Each preferred schedule contains a display `label`, target departure and arrival times, an `arrival_day_offset`, independently configurable departure/arrival tolerance minutes, and optional actual origin/destination airport IATA codes. Preferred matches are selected from the complete eligible response before the cheapest `top_n` slice is applied.

Preferred prices are persisted independently by leg, departure date, target times and actual airports. The first successful match is retained as the baseline; the most recent successful match is used for the previous-price comparison. Tolerance-only changes keep the same history identity.

## Flight snapshot

Planned fields:

| Field | Meaning |
|---|---|
| `run_id` | Collection run identifier |
| `leg_id` | Configured route identifier |
| `flight_signature` | Stable signature from all segments, date and times |
| `flight_codes` | One or more flight numbers |
| `carrier_codes` | Operating/marketing airline codes |
| `origin_airport_iata` | Actual origin airport |
| `destination_airport_iata` | Actual destination airport |
| `departure_date` | Departure date |
| `etd_local` | Scheduled local departure time |
| `eta_local` | Scheduled local arrival time |
| `duration_minutes` | Journey duration |
| `segment_count` | Number of flight segments |
| `is_direct` | Derived from `segment_count == 1` |
| `connection_airports` | Ordered connection-airport IATA codes |
| `layover_minutes` | Sum of waiting minutes between adjacent segments |
| `return_itinerary` | Complete return-direction itinerary for a round-trip combination, including its seat hint |
| `base_price_cny` | Base fare |
| `tax_cny` | Taxes and fees |
| `total_price_cny` | Primary comparison and alert value |
| `remaining_seats` | Displayed seat-availability hint |
| `seat_availability` | Structured overall hint: count/text, scarcity wording and inventory-review flag |
| `outbound_seat_availability` | Structured outbound-direction hint |
| `free_baggage_piece` | Free checked-baggage piece count |
| `free_baggage_weight` | Free checked-baggage weight |
| `source_domain` | Quoted supplier/source domain |
| `captured_at` | Asia/Shanghai capture timestamp |

`flight_signature` must not use only the flight number. It includes all outbound and, for round trips, all return segment flight numbers, dates, airports and scheduled times. Round-trip `total_price_cny` is Qunar's combination total and is never synthesized from two one-way observations.

Seat availability is advisory. In observed Qunar payloads, `journey.seatInfo.nums` is the overall round-trip hint and each `journey.trips[*].seatInfo.nums` is the direction-level hint. The value `9` is treated as a capped platform hint and rendered as “9 or more”, not as an exact count. `ticketInsufficient` is retained as a review warning rather than interpreted as a guaranteed sold-out state.

## Collection run

Planned fields include `run_id`, start/end time, overall state, per-leg state, response completion flag, result count, error category and raw-response reference.
