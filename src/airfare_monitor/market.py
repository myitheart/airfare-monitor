"""Resolve which public search site should collect a configured route."""

from __future__ import annotations

from functools import lru_cache

import airportsdata

from .errors import CollectionError
from .models import LegConfig


@lru_cache(maxsize=1)
def _iata_airports() -> dict[str, dict]:
    return airportsdata.load("IATA")


# 常见多机场城市（Metropolitan Area Codes）归属国家映射，
# airportsdata 仅包含实体机场，不包含纯城市聚合代码。
_METRO_CITY_COUNTRIES = {
    "BJS": "CN",  # 北京（PEK/PKX）
    "SHA": "CN",  # 上海（SHA/PVG）
    "CAN": "CN",  # 广州
    "SZX": "CN",  # 深圳
    "CTU": "CN",  # 成都（CTU/TFU）
    "WUH": "CN",  # 武汉
    "CKG": "CN",  # 重庆
    "TYO": "JP",  # 东京（HND/NRT）
    "OSA": "JP",  # 大阪（KIX/ITM）
    "SPK": "JP",  # 札幌
    "SEL": "KR",  # 首尔（ICN/GMP）
    "BKK": "TH",  # 曼谷（BKK/DMK）
    "LON": "GB",  # 伦敦（LHR/LGW/LCY等）
    "PAR": "FR",  # 巴黎（CDG/ORY）
    "ROM": "IT",  # 罗马（FCO/CIA）
    "MIL": "IT",  # 米兰（MXP/LIN/BGY）
    "MOW": "RU",  # 莫斯科（SVO/DME/VKO）
    "NYC": "US",  # 纽约（JFK/EWR/LGA）
    "WAS": "US",  # 华盛顿（IAD/DCA/BWI）
    "CHI": "US",  # 芝加哥（ORD/MDW）
    "YTO": "CA",  # 多伦多（YYZ/YTZ）
    "YVR": "CA",  # 温哥华
}


def _country_for_code(code: str, child_airports: tuple[str, ...] | None = None) -> str | None:
    airports = _iata_airports()
    record = airports.get(code)
    if record and "country" in record:
        return record["country"]
    if code in _METRO_CITY_COUNTRIES:
        return _METRO_CITY_COUNTRIES[code]
    if child_airports:
        for child in child_airports:
            child_record = airports.get(child)
            if child_record and "country" in child_record:
                return child_record["country"]
    return None


def resolve_market(leg: LegConfig) -> str:
    """Return ``domestic`` or ``international`` for one leg.

    Explicit configuration wins. Automatic classification is deliberately
    strict: both endpoints must exist in the bundled IATA dataset, otherwise
    the operator must choose a market instead of silently querying the wrong
    website.
    """
    if leg.market in {"domestic", "international"}:
        return leg.market
    origin_country = _country_for_code(leg.origin_airport_iata, leg.origin_airports)
    destination_country = _country_for_code(leg.destination_airport_iata, leg.destination_airports)
    missing = [
        code
        for code, country in (
            (leg.origin_airport_iata, origin_country),
            (leg.destination_airport_iata, destination_country),
        )
        if country is None
    ]
    if missing:
        raise CollectionError(
            f"无法自动判断 {'/'.join(missing)} 所属国家；请在该航程配置 market: domestic 或 international"
        )
    return "domestic" if origin_country == destination_country == "CN" else "international"
