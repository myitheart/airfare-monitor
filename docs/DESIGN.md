# System design

## Collection flow

1. Load all enabled legs and validate airport codes, dates and ETD windows.
2. Reuse one isolated Chromium profile and process legs serially.
3. Start listening for `/touch/api/inter/wwwsearch` before navigation.
4. Open the exact route/date search page and let the page generate anonymous session data and request parameters.
5. Consume incremental responses until `result.ctrlInfo.completed == true`.
6. Parse flights, keep direct journeys inside the configured ETD window, and sort by CNY total price.
7. Retain at most `top_n` results for each leg.
8. Save the run and flight snapshots to SQLite.
9. Compare each leg's minimum total price with its configured expectation and the previous run.
10. Generate one Excel workbook and one email summary covering all enabled legs.
11. If any leg meets its threshold, use the low-price subject format.

The search-page URL is configuration, not a reimplemented API call. The collector lets Chromium generate Bella, queryId, st and anonymous cookies, and only reads browser-observed responses. A schema mismatch fails closed and must be calibrated against a sanitized completed payload.

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
