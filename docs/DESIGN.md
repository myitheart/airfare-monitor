# System design

## Collection flow

1. Load all enabled legs and validate airport codes, dates, ETD windows and optional market overrides.
2. Resolve `market: auto` from the IATA country dataset: mainland-China domestic routes use Tongcheng; all cross-border routes use Qunar.
3. Reuse one isolated Chromium profile and process legs serially.
4. For Qunar, operate the normal search form and start listening before clicking search. For Tongcheng, navigate directly to its public route/date result URL and wait for the structured Nuxt page state to finish combining the server-rendered initial group with its continuation group.
5. Qunar consumes incremental `/touch/api/inter/wwwsearch` responses until `result.ctrlInfo.completed == true`.
6. Tongcheng direct navigation is complete only when `window.__NUXT__.state.book1.dataflag == last`, its route/date match the requested leg, and `flightLists` is a list. The lower-level parser also retains strict support for recorded `apiSuccess=true / dataflag=all` responses.
7. Parse flights, keep direct journeys inside the configured ETD window, and sort by CNY tax-inclusive total price.
8. Retain at most `top_n` results for each leg.
9. Save the run and flight snapshots to SQLite.
10. Compare each leg's minimum total price with its configured expectation and the previous run.
11. Generate one Excel workbook and one email summary covering all enabled legs.
12. If any leg meets its threshold, use the low-price subject format.

Search-page URLs are configuration, not reimplemented API calls. Chromium generates each site's anonymous session parameters and cookies; the collector only reads browser-observed responses. It never replays cookies or captured request tokens. A schema mismatch fails closed and must be calibrated against a sanitized completed payload.

For Tongcheng, the recorded `lcp` field matches the fare displayed as “¥…起”. Adult airport and fuel charges are provided separately as `pt` and `ot`, so monitoring, ranking and threshold checks use `lcp + pt + ot`. Missing tax fields make that flight unparseable rather than silently treating the displayed fare as a total.

## Recommended process model

- One scheduler process.
- One persistent Chromium process and isolated browser profile.
- All enabled routes are collected serially to avoid overlapping search sessions.
- One consolidated email per configured interval, not one email per leg.
- No new run starts while the previous run is still active.
- A persistent OS file lock prevents a second scheduler process from overlapping.

## Run states

- `success`: all enabled legs completed.
- `partial`: at least one leg completed and at least one failed.
- `failed`: no leg produced a complete response.
- `threshold_hit`: delivery flag applied in addition to success/partial when at least one leg meets its configured price.

## Alert policy

A threshold hit means:

```text
minimum eligible total price <= expected_total_price_cny
```

The comparison must use total CNY price after taxes when available. A threshold hit should be rechecked once after the configured delay before the email is marked as a confirmed low-price alert.

## Failure handling

- Never treat an incomplete incremental response as a completed collection.
- Retry a failed leg once after rebuilding the page listener.
- Restart the isolated browser after consecutive failures.
- If a CAPTCHA or device-verification page appears, stop that leg and record a manual-attention status.
- Send the consolidated email even for a partial run, clearly marking failed legs.
