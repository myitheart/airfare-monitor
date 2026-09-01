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
| `expected_total_price_cny` | yes | Psychological total-price threshold |
| `top_n` | yes | Number of cheapest eligible flights retained |
| `adult_count` | yes | Adult passenger count used for pricing |
| `child_count` | yes | Child passenger count used for pricing |
| `cabin_class` | yes | Requested cabin class |
| `preferred_schedules` | no | Exact schedules shown separately with live prices in each email |

Each preferred schedule contains a display `label`, target departure and arrival times, an `arrival_day_offset`, independently configurable departure/arrival tolerance minutes, and optional actual origin/destination airport IATA codes. Preferred matches are selected from the complete eligible response before the cheapest `top_n` slice is applied.

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
| `base_price_cny` | Base fare |
| `tax_cny` | Taxes and fees |
| `total_price_cny` | Primary comparison and alert value |
| `remaining_seats` | Displayed seat-availability hint |
| `free_baggage_piece` | Free checked-baggage piece count |
| `free_baggage_weight` | Free checked-baggage weight |
| `source_domain` | Quoted supplier/source domain |
| `captured_at` | Asia/Shanghai capture timestamp |

`flight_signature` must not use only the flight number. It should include all segment flight numbers, segment dates, airports and scheduled times so that cross-day or retimed journeys are not merged incorrectly.

## Collection run

Planned fields include `run_id`, start/end time, overall state, per-leg state, response completion flag, result count, error category and raw-response reference.
