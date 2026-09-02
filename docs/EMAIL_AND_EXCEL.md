# Email and Excel delivery design

## Subject rules

Normal update:

```text
[航价监控] N程更新 | 2026-10-01 08:30
```

When every enabled item is a round trip, the label is `N组往返更新`.

One or more confirmed threshold hits:

```text
[低价命中][命中数/N程] PVG-KUL ¥1,420 ≤ ¥1,500 | 2026-10-01 08:30
```

For an all-round-trip configuration, the counter unit is `组` and the route uses `↔`.

Partial collection:

```text
[航价监控][部分失败] N程更新 | 2026-10-01 08:30
```

Priority is: confirmed threshold hit, partial/failed marker, normal update.

## Email body

The body should be readable on a phone and contain:

1. Run time and overall status.
2. One compact block per leg:
   - route, trip type, outbound/return dates and departure windows;
   - expected threshold;
   - current minimum eligible total price;
   - difference from threshold;
   - difference from previous run;
   - up to five cheapest combinations with both directions' flight numbers, ETD, itinerary type, connection airports, total layover, round-trip total and overall/outbound/return seat hints;
   - configured preferred schedules with target times, actual matched times, live total prices and seat hints, even when outside `top_n`;
   - success, partial, failed or manual-attention status.
3. A clear reminder that prices are observed listings and must be confirmed in the app.

Preferred schedules may define independent departure and arrival tolerance minutes. Matching first requires the configured actual airports, then chooses the itinerary with the smallest combined time deviation inside both tolerance limits; total price is only a tie-breaker.

Preferred schedules are the primary section in each route card. For every configured schedule the email shows current price, first observed price and timestamp, change from first, previous observed price and timestamp, and change from previous. Up to five cheapest flights are presented afterwards as fallback options. Every fallback combination has the same first/previous price comparisons even when no preferred schedule is configured. A missing preferred match remains visible and does not replace the first or previous successful price reference.

## Workbook layout

### `本次汇总`

One row per leg:

```text
航程 | 行程类型 | 出发机场 | 到达机场 | 去程日期 | 去程ETD时间窗
返程日期 | 返程ETD时间窗 | 心理价位
本次最低总价 | 与阈值差额 | 较上次变化 | 是否命中 | 符合航班数
采集状态 | 采集时间
```

### `航程1` to `航程N`

One sheet is generated dynamically for every enabled leg.

Up to 10 eligible flights per sheet. When connections are enabled, direct and connecting itineraries share the same total-price ranking:

```text
排名 | 航班号 | 航司 | 出发机场 | 到达机场 | 出发日期
ETD | ETA | 飞行时长 | 航段数 | 行程类型 | 中转机场 | 中转等待(分钟)
基础票价 | 税费 | 总价 | 币种
余票提示 | 免费行李件数 | 免费行李重量 | 报价来源 | 采集时间
返程航班号 | 返程航司 | 返程出发机场 | 返程到达机场 | 返程日期
返程ETD | 返程ETA | 返程飞行时长 | 返程行程类型 | 返程中转信息
去程余票提示 | 返程余票提示
```

Seat counts are platform hints. A returned value of `9` is displayed as `9张或以上（平台提示）`; it is not represented as an exact remaining inventory count.

### `24小时历史`

One row per leg and collection time with minimum total price, allowing a simple trend chart later.

## Attachment naming

```text
airfare-monitor_YYYYMMDD_HHMM.xlsx
```
