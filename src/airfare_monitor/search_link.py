"""为航程构造对应来源网站的搜索直达链接（与采集口径一致）。"""

from __future__ import annotations

from urllib.parse import quote

from .config import AppSettings
from .errors import CollectionError
from .market import resolve_market
from .models import LegConfig

# 去哪儿单程模板在默认配置里是「落地页」（采集器靠页面内表单交互选择航线），
# 直达链接需要显式补上查询参数才能预填航线与日期。
_QUNAR_ONEWAY_PARAMS = (
    "fromCity={origin_name}&toCity={destination_name}&fromDate={date}"
    "&fromCode={origin}&toCode={destination}"
    "&adultNum={adult_count}&childNum={child_count}&cabinClass={cabin_class}"
    "&from=flight_int_search&isInter=true&lowestPrice=null"
)


def search_url_for(leg: LegConfig, settings: AppSettings) -> str:
    """返回该航程在来源网站的搜索结果页链接。

    分支与采集器 collect() 完全一致：国内单程 → 同程；跨境/国际单程 →
    去哪儿；往返（仅国际）→ 去哪儿往返比对页。
    """
    from .collector import build_roundtrip_search_url, build_search_url, build_tongcheng_search_url

    market = resolve_market(leg)
    if leg.is_round_trip:
        if market == "domestic":
            raise CollectionError("往返合计价目前只支持去哪儿跨境/国际航线")
        return build_roundtrip_search_url(settings.browser.roundtrip_search_url_template, leg)
    if market == "domestic":
        return build_tongcheng_search_url(settings.browser.tongcheng_search_url_template, leg)
    url = build_search_url(settings.browser.search_url_template, leg)
    if "?" not in url:
        values = {
            "origin": quote(leg.origin_airport_iata),
            "destination": quote(leg.destination_airport_iata),
            "origin_name": quote(leg.origin_name_zh or leg.origin_airport_iata),
            "destination_name": quote(leg.destination_name_zh or leg.destination_airport_iata),
            "date": quote(leg.departure_date.isoformat()),
            "adult_count": leg.adult_count,
            "child_count": leg.child_count,
            "cabin_class": quote(leg.cabin_class),
        }
        url = url.rstrip("/") + "/?" + _QUNAR_ONEWAY_PARAMS.format(**values)
    return url


def search_site_label(leg: LegConfig) -> str:
    """按钮/文案用的来源名称：同程 或 去哪儿。"""
    return "同程" if resolve_market(leg) == "domestic" else "去哪儿"
