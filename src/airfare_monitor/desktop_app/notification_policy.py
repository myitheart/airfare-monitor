"""Quiet-by-default desktop alert decisions from immutable worker events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .events import CycleFinished, FatalError, MailDeliveryFailed, ManualAttentionRequested


@dataclass(frozen=True, slots=True)
class DesktopAlert:
    title: str
    message: str
    target_page: int


def alert_for_event(
    event: object,
    *,
    low_price_details: Sequence[Mapping[str, object]] | None = None,
) -> DesktopAlert | None:
    if isinstance(event, ManualAttentionRequested):
        return DesktopAlert("需要人工完成页面确认", "请打开系统状态处理受影响航程。", 4)
    if isinstance(event, MailDeliveryFailed):
        return DesktopAlert("邮件发送失败", "价格和 Excel 已保存，请检查通知设置。", 3)
    if isinstance(event, FatalError):
        return DesktopAlert("监控运行异常", "请打开系统状态检查运行设置。", 4)
    if isinstance(event, CycleFinished):
        confirmed = event.report.threshold_confirmed_leg_ids
        if confirmed:
            if low_price_details is None:
                low_price_details = tuple(
                    _result_details(result) for result in event.report.confirmed_hits
                )
            if low_price_details:
                return _low_price_alert(low_price_details)
            # An empty collection means every confirmed price was a recent
            # duplicate. Keep the desktop quiet but still surface failures.
        succeeded = sum(item.status.value == "success" for item in event.report.legs)
        total = event.total_legs or len(event.report.legs)
        if any(item.status.value == "manual_attention" for item in event.report.legs):
            return None  # ManualAttentionRequested already gives the actionable alert.
        if succeeded < total:
            return DesktopAlert(
                "本轮查询未全部完成",
                f"成功 {succeeded}/{total}；已完成结果仍保留在价格历史中。",
                4,
            )
    return None


def _result_details(result: object) -> dict[str, object]:
    leg = result.leg
    price = result.minimum_total_cny
    threshold = leg.expected_total_price_cny
    savings = threshold - price if threshold is not None and price is not None else None
    arrow = "⇄" if leg.is_round_trip else "→"
    return {
        "route_code": f"{leg.origin_airport_iata} {arrow} {leg.destination_airport_iata}",
        "actual_price_cny": str(price) if price is not None else "",
        "threshold_price_cny": str(threshold) if threshold is not None else "",
        "savings_cny": str(max(Decimal("0"), savings)) if savings is not None else "",
    }


def _low_price_alert(details: Sequence[Mapping[str, object]]) -> DesktopAlert:
    first = details[0]
    route = str(first.get("route_code") or "航程")
    price = _price(first.get("actual_price_cny"))
    threshold = _price(first.get("threshold_price_cny"))
    savings_value = _decimal(first.get("savings_cny"))
    if len(details) == 1:
        comparison = (
            f"，低于心理价 {_price(savings_value)}"
            if savings_value is not None and savings_value > 0 else ""
        )
        message = f"含税 {price} · 心理价 {threshold}{comparison}。点击查看航班候选。"
        return DesktopAlert(f"发现低价 · {route}", message, 0)
    return DesktopAlert(
        f"发现 {len(details)} 条低价航程",
        f"{route} 含税 {price}；另有 {len(details) - 1} 条航程命中心理价。",
        0,
    )


def _price(value: object) -> str:
    parsed = _decimal(value)
    return f"¥{parsed:,.0f}" if parsed is not None else "—"


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None
