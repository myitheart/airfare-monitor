# Email and Excel delivery design

## Subject rules

Normal update:

```text
[航价监控] N程更新 | 2026-10-01 08:30
```

One or more confirmed threshold hits:

```text
[低价命中][命中数/N程] PVG-KUL ¥1,420 ≤ ¥1,500 | 2026-10-01 08:30
```

Partial collection:

```text
[航价监控][部分失败] N程更新 | 2026-10-01 08:30
```

Priority is: confirmed threshold hit, partial/failed marker, normal update.

## Email body

The body should be readable on a phone and contain:

1. Run time and overall status.
2. One compact block per leg:
   - route and departure date;
   - expected threshold;
   - current minimum direct total price;
   - difference from threshold;
   - difference from previous run;
   - cheapest three flight numbers with ETD and total prices;
   - success, partial, failed or manual-attention status.
3. A clear reminder that prices are observed listings and must be confirmed in the app.

## Workbook layout

### `本次汇总`

One row per leg:

```text
航程 | 出发机场 | 到达机场 | 出发日期 | ETD时间窗 | 心理价位
本次最低总价 | 与阈值差额 | 较上次变化 | 是否命中 | 符合航班数
采集状态 | 采集时间
```

### `航程1` to `航程N`

One sheet is generated dynamically for every enabled leg.

Up to 10 eligible direct flights per sheet:

```text
排名 | 航班号 | 航司 | 出发机场 | 到达机场 | 出发日期
ETD | ETA | 飞行时长 | 基础票价 | 税费 | 总价 | 币种
余票提示 | 免费行李件数 | 免费行李重量 | 报价来源 | 采集时间
```

### `24小时历史`

One row per leg and collection time with minimum total price, allowing a simple trend chart later.

## Attachment naming

```text
airfare-monitor_YYYYMMDD_HHMM.xlsx
```
