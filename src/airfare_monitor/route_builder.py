"""非交互的航程构造：把 CLI 参数解析为 LegConfig（供终端/AI 调用）。

与 GUI 向导共享同一套领域设施：AirportCatalog 负责机场/城市聚合解析，
resolve_market 判定来源，RouteRepository 负责原子校验写入——这里只做
「字符串参数 → LegConfig」的一层确定性翻译，错误信息单行中文并给出
候选，便于 AI 代理自行纠正。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from .desktop_app.airport_catalog import AirportCatalog, AirportRecord
from .market import resolve_market
from .models import EtdWindow, LegConfig, PreferredSchedule


@dataclass(frozen=True, slots=True)
class RouteDraft:
    leg: LegConfig
    notes: tuple[str, ...]


def resolve_endpoint(catalog: AirportCatalog, value: str) -> AirportRecord:
    """按 IATA 码 / 城市码 / 中文名解析端点；歧义时列出候选。

    解析优先级与 GUI 向导联想一致：IATA 精确 > 显示名精确（如"上海浦东"）>
    裸城市名（如"上海"→城市聚合，同城全部机场）> 唯一搜索命中。
    """
    text = value.strip()
    if not text:
        raise ValueError("机场或城市不能为空")
    direct = catalog.by_iata(text, allow_city=True)
    if direct is not None:
        return direct
    normalized = text.lower().replace(" ", "")
    results = catalog.search(text, include_cities=True)
    for record in results:
        if normalized == record.display_name_zh.lower().replace(" ", ""):
            return record
    for city in catalog.cities:
        if normalized in (city.city_name_zh.lower(), city.airport_iata.lower()):
            return city
    if len(results) == 1:
        return results[0]
    if not results:
        raise ValueError(f"未找到机场或城市：{value}")
    candidates = "、".join(_endpoint_label(record) for record in results[:5])
    raise ValueError(f"“{value}” 有多个匹配：{candidates}；请改用 IATA 代码精确指定")


def build_leg(
    catalog: AirportCatalog,
    *,
    origin: str | None,
    destination: str | None,
    departure_date: str | None,
    return_date: str | None,
    etd_start: str | None,
    etd_end: str | None,
    direct_only: bool | None,
    threshold: str | None,
    adults: int | None,
    children: int | None,
    cabin: str | None,
    preferred: list[str] | None,
    route_id: str | None,
    existing: LegConfig | None = None,
    taken_ids: set[str] | None = None,
) -> RouteDraft:
    """构造（或基于 existing 部分修改）一条 LegConfig。

    所有参数为 None 时沿用 existing 的值（编辑模式）；缺省且无 existing
    时使用与 GUI 向导一致的默认值。
    """
    notes: list[str] = []
    # 端点处理：显式传入才解析；编辑未动端点时原样继承字段。
    # 注意 SHA/BJS 等聚合码与成员机场码重名（SHA 既是上海城市码又是虹桥），
    # 重解析会把「城市聚合」降级成成员机场，因此绝不重解析未修改的端点。
    if origin is not None:
        origin_record = resolve_endpoint(catalog, origin)
        origin_code = origin_record.airport_iata
        origin_name = _city_label(origin_record)
        origin_airports = origin_record.child_airports if origin_record.is_city else None
        notes.append(f"出发地：{_endpoint_label(origin_record)}")
        if origin_record.is_city:
            notes.append("多机场聚合：同城全部机场统一比价")
    elif existing is not None:
        origin_code, origin_name, origin_airports = (
            existing.origin_airport_iata,
            existing.origin_name_zh,
            existing.origin_airports,
        )
    else:
        raise ValueError("缺少出发地；新增时必须提供 --from")
    if destination is not None:
        destination_record = resolve_endpoint(catalog, destination)
        destination_code = destination_record.airport_iata
        destination_name = _city_label(destination_record)
        destination_airports = destination_record.child_airports if destination_record.is_city else None
        notes.append(f"目的地：{_endpoint_label(destination_record)}")
        if destination_record.is_city:
            notes.append("多机场聚合：目的地同城全部机场统一比价")
    elif existing is not None:
        destination_code, destination_name, destination_airports = (
            existing.destination_airport_iata,
            existing.destination_name_zh,
            existing.destination_airports,
        )
    else:
        raise ValueError("缺少目的地；新增时必须提供 --to")

    departure = _date(departure_date, existing.departure_date if existing else None, "出发日期")
    parsed_return = _optional_date(return_date, existing.return_date if existing else None)

    if route_id:
        leg_id = route_id
    elif existing:
        leg_id = existing.id
    else:
        leg_id = _auto_id(origin_code, destination_code, departure, taken_ids or set())

    schedules = _preferred_schedules(preferred, existing)
    if preferred:
        preferred_origin = None if origin_airports else origin_code
        preferred_destination = None if destination_airports else destination_code
        schedules = tuple(
            replace(item, origin_airport_iata=preferred_origin, destination_airport_iata=preferred_destination)
            for item in schedules
        )
        notes.append(f"重点班次：{'、'.join(item.label for item in schedules)}")

    leg = LegConfig(
        id=leg_id,
        enabled=existing.enabled if existing else True,
        origin_name_zh=origin_name,
        destination_name_zh=destination_name,
        origin_airport_iata=origin_code,
        destination_airport_iata=destination_code,
        origin_airports=origin_airports,
        destination_airports=destination_airports,
        departure_date=departure,
        return_date=parsed_return,
        etd_window=EtdWindow(*_etd_window(etd_start, etd_end, _existing_window(existing))),
        return_etd_window=_return_window(parsed_return, existing),
        return_direct_only=(existing.return_direct_only if existing and existing.return_direct_only is not None else True),
        direct_only=direct_only if direct_only is not None else (existing.direct_only if existing else True),
        expected_total_price_cny=_threshold(threshold, existing),
        top_n=existing.top_n if existing else 5,
        adult_count=adults if adults is not None else (existing.adult_count if existing else 1),
        child_count=children if children is not None else (existing.child_count if existing else 0),
        cabin_class=cabin or (existing.cabin_class if existing else "economy"),
        market=existing.market if existing else "auto",
        preferred_schedules=schedules,
    )
    try:
        market = resolve_market(leg)
    except ValueError as exc:
        raise ValueError(f"无法判定航线来源（国内/国际）：{exc}；可用 --market 显式指定") from exc
    if market == "domestic" and leg.is_round_trip:
        raise ValueError("往返合计价目前只支持去哪儿跨境/国际航线；国内往返请分两条单程监控")
    notes.append(f"来源：{'同程（国内）' if market == 'domestic' else '去哪儿（国际/跨境）'}")
    return RouteDraft(leg=leg, notes=tuple(notes))


def parse_preferred(value: str) -> PreferredSchedule:
    """解析 ``标签,HH:MM,HH:MM[,容差分钟]`` 为重点班次。"""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) not in (3, 4):
        raise ValueError(f"重点班次格式应为 标签,HH:MM,HH:MM[,容差分钟]：{value}")
    label, departure_text, arrival_text = parts[0], parts[1], parts[2]
    tolerance = int(parts[3]) if len(parts) == 4 else 30
    if not label:
        raise ValueError(f"重点班次缺少标签：{value}")
    return PreferredSchedule(
        label=label,
        departure_time=_time(departure_text, "重点班次起飞时间"),
        arrival_time=_time(arrival_text, "重点班次到达时间"),
        departure_tolerance_minutes=tolerance,
        arrival_tolerance_minutes=tolerance,
    )


def leg_to_summary(leg: LegConfig) -> dict[str, object]:
    """供 --json 输出的航程摘要（字段与 GUI 卡片口径一致）。"""
    return {
        "id": leg.id,
        "enabled": leg.enabled,
        "origin": f"{leg.origin_name_zh} {leg.origin_airport_iata}",
        "destination": f"{leg.destination_name_zh} {leg.destination_airport_iata}",
        "origin_airports": list(leg.origin_airports or ()),
        "destination_airports": list(leg.destination_airports or ()),
        "departure_date": leg.departure_date.isoformat(),
        "return_date": leg.return_date.isoformat() if leg.return_date else None,
        "etd_window": f"{leg.etd_window.start:%H:%M}-{leg.etd_window.end:%H:%M}",
        "direct_only": leg.direct_only,
        "expected_total_price_cny": str(leg.expected_total_price_cny) if leg.expected_total_price_cny is not None else None,
        "adults": leg.adult_count,
        "children": leg.child_count,
        "cabin": leg.cabin_class,
        "preferred": [
            {
                "label": item.label,
                "departure": f"{item.departure_time:%H:%M}",
                "arrival": f"{item.arrival_time:%H:%M}",
                "tolerance_minutes": item.departure_tolerance_minutes,
            }
            for item in leg.preferred_schedules
        ],
    }


def _endpoint_label(record: AirportRecord) -> str:
    if record.is_city:
        children = "/".join(record.child_airports)
        return f"{record.display_name_zh}（城市聚合 {record.airport_iata}，含 {children}）"
    airport = record.airport_name_zh or record.display_name_zh
    return f"{record.display_name_zh} {record.airport_iata}（{airport}）"


def _city_label(record: AirportRecord) -> str:
    return record.city_name_zh or record.display_name_zh


def _auto_id(origin: str, destination: str, departure: date, taken: set[str]) -> str:
    base = f"{origin.lower()}-{destination.lower()}-{departure.strftime('%Y%m%d')}"
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _date(value: str | None, fallback: date | None, label: str) -> date:
    if value in (None, ""):
        if fallback is not None:
            return fallback
        raise ValueError(f"缺少 {label}；格式 YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label}必须是 YYYY-MM-DD：{value}") from exc


def _optional_date(value: str | None, fallback: date | None) -> date | None:
    if value == "":
        return None
    return _date(value, fallback, "返程日期") if value else fallback


def _time(value: str, label: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError(f"{label}必须是 HH:MM：{value}") from exc


def _threshold(value: str | None, existing: LegConfig | None) -> Decimal | None:
    if value in (None, ""):
        return existing.expected_total_price_cny if existing else None
    try:
        threshold = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"心理价位必须是金额数字：{value}") from exc
    if threshold <= 0:
        raise ValueError("心理价位必须大于 0")
    return threshold


def _existing_window(existing: LegConfig | None) -> tuple[time, time] | None:
    if existing is None:
        return None
    return existing.etd_window.start, existing.etd_window.end


def _return_window(parsed_return: date | None, existing: LegConfig | None) -> EtdWindow | None:
    if parsed_return is None:
        return existing.return_etd_window if existing else None
    if existing is not None and existing.return_etd_window is not None:
        return existing.return_etd_window
    return EtdWindow(time(0, 0), time(23, 59))


def _etd_window(
    start: str | None,
    end: str | None,
    fallback: tuple[time, time] | None,
) -> tuple[time, time]:
    if start is None and end is None:
        return fallback or (time(0, 0), time(23, 59))
    if start is None or end is None:
        raise ValueError("出发时段需要同时提供 --etd-start 与 --etd-end（HH:MM）")
    window = (_time(start, "出发时段起点"), _time(end, "出发时段终点"))
    if window[0] > window[1]:
        raise ValueError("出发时段起点必须早于终点")
    return window


def _preferred_schedules(preferred: list[str] | None, existing: LegConfig | None) -> tuple[PreferredSchedule, ...]:
    if not preferred:
        return existing.preferred_schedules if existing else ()
    return tuple(parse_preferred(item) for item in preferred)
