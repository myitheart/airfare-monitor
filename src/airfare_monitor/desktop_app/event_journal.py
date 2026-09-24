"""Persist a small, redacted activity journal for the desktop overview."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from threading import Lock

from ..models import LegResult
from ..storage import SQLiteStore
from .events import (
    CycleFinished,
    CycleStarted,
    FatalError,
    LegFinished,
    ManualAttentionRequested,
    MailDeliveryFailed,
    RoutesExpired,
    VerificationBrowserOpened,
)


class AppEventJournal:
    """Translate worker events into stable, user-facing and non-sensitive records."""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self._alert_lock = Lock()
        self._low_price_alerts: dict[str, tuple[dict[str, object], ...]] = {}

    def initialize(self) -> None:
        self.store.initialize()
        self.store.prune_app_events(30)

    def record(self, event: object) -> None:
        payload = _event_payload(event)
        if payload is None:
            return
        event_type, severity, message, leg_id, occurred_at = payload
        self.store.record_app_event(
            event_type=event_type,
            severity=severity,
            message=message,
            leg_id=leg_id,
            occurred_at=occurred_at,
        )
        if isinstance(event, CycleFinished):
            recorded = tuple(
                details
                for result in event.report.confirmed_hits
                if (details := self._record_low_price(event.report.run_id, result, event.report.finished_at))
                is not None
            )
            with self._alert_lock:
                self._low_price_alerts[event.report.run_id] = recorded
                while len(self._low_price_alerts) > 32:
                    self._low_price_alerts.pop(next(iter(self._low_price_alerts)))

    def low_price_alert_details(self, run_id: str) -> tuple[dict[str, object], ...]:
        """Return only newly noteworthy hits from a completed run."""
        with self._alert_lock:
            return self._low_price_alerts.get(run_id, ())

    def _record_low_price(
        self,
        run_id: str,
        result: LegResult,
        occurred_at: datetime,
    ) -> dict[str, object] | None:
        price = result.minimum_total_cny
        threshold = result.leg.expected_total_price_cny
        if price is None or threshold is None:
            return None
        previous = self.store.latest_app_event("low_price_confirmed", result.leg.id)
        if not _is_noteworthy_low_price(previous, price, threshold, occurred_at):
            return None

        arrow = "⇄" if result.leg.is_round_trip else "→"
        route_code = (
            f"{result.leg.origin_airport_iata} {arrow} "
            f"{result.leg.destination_airport_iata}"
        )
        route_name = (
            f"{result.leg.origin_name_zh or result.leg.origin_airport_iata} {arrow} "
            f"{result.leg.destination_name_zh or result.leg.destination_airport_iata}"
        )
        savings = max(Decimal("0"), threshold - price)
        details: dict[str, object] = {
            "run_id": run_id,
            "route_code": route_code,
            "route_name": route_name,
            "actual_price_cny": str(price),
            "threshold_price_cny": str(threshold),
            "savings_cny": str(savings),
            "captured_at": result.captured_at.isoformat(timespec="seconds"),
        }
        difference = (
            f"，低于心理价 {_price(savings)}"
            if savings > 0 else "，已达到心理价"
        )
        self.store.record_app_event(
            event_type="low_price_confirmed",
            severity="notice",
            leg_id=result.leg.id,
            message=f"{route_code} 命中心理价：含税 {_price(price)}{difference}",
            details=details,
            occurred_at=occurred_at,
        )
        return details

    def record_settings_changed(self) -> None:
        self.store.record_app_event(
            event_type="settings_changed",
            severity="info",
            message="运行设置已更新，将从下一轮查询开始生效",
        )

    def record_routes_changed(self, enabled_count: int) -> None:
        self.store.record_app_event(
            event_type="routes_changed",
            severity="info",
            message=f"航程配置已更新，当前启用 {enabled_count}/10 条",
        )


def _event_payload(
    event: object,
) -> tuple[str, str, str, str | None, datetime | None] | None:
    if isinstance(event, CycleStarted):
        return (
            "cycle_started",
            "info",
            f"开始串行查询 {event.total_legs} 条航程",
            None,
            event.started_at,
        )
    if isinstance(event, LegFinished):
        route = (
            f"{event.result.leg.origin_airport_iata} → "
            f"{event.result.leg.destination_airport_iata}"
        )
        status = event.result.status.value
        if status == "failed":
            return "leg_failed", "warning", f"{route} 查询失败，本次价格未采用", event.result.leg.id, None
        if status == "manual_attention":
            # ManualAttentionRequested records the single public attention message.
            return None
        return None
    if isinstance(event, ManualAttentionRequested):
        return (
            "manual_attention",
            "warning",
            f"航程 {event.leg_id} 需要人工完成页面确认",
            event.leg_id,
            None,
        )
    if isinstance(event, CycleFinished):
        succeeded = sum(item.status.value == "success" for item in event.report.legs)
        total = event.total_legs or len(event.report.legs)
        duration = max(0, int((event.report.finished_at - event.report.started_at).total_seconds()))
        severity = "info" if succeeded == total else "warning"
        return (
            "cycle_finished",
            severity,
            f"本轮查询完成：成功 {succeeded}/{total}，耗时 {duration} 秒",
            None,
            event.report.finished_at,
        )
    if isinstance(event, RoutesExpired):
        count = len(event.legs)
        if count == 1:
            leg = event.legs[0]
            route = f"{leg.origin_airport_iata} → {leg.destination_airport_iata}"
            message = f"{route} 已超过出发日期，监控已自动暂停"
            leg_id = leg.id
        else:
            message = f"{count} 条航程已超过出发日期，监控已自动暂停"
            leg_id = None
        return "routes_expired", "notice", message, leg_id, event.checked_at
    if isinstance(event, FatalError):
        # Never persist raw exception text; it can include URLs or browser details.
        return "fatal_error", "error", "监控运行异常，请打开系统状态检查", None, None
    if isinstance(event, MailDeliveryFailed):
        return "mail_failed", "warning", "价格和报告已保存，但邮件发送失败；请检查通知设置", None, None
    if isinstance(event, VerificationBrowserOpened):
        return "verification_opened", "notice", "已打开独立可见浏览器，等待人工完成页面确认", event.leg_id, None
    return None


def _is_noteworthy_low_price(
    previous: dict[str, object] | None,
    price: Decimal,
    threshold: Decimal,
    occurred_at: datetime,
) -> bool:
    if previous is None:
        return True
    details = previous.get("details")
    if not isinstance(details, dict):
        return True
    previous_price = _decimal(details.get("actual_price_cny"))
    previous_threshold = _decimal(details.get("threshold_price_cny"))
    try:
        previous_at = datetime.fromisoformat(str(previous.get("occurred_at")))
    except (TypeError, ValueError):
        return True
    if previous_price is None or previous_threshold != threshold:
        return True
    age = occurred_at - previous_at
    return not (timedelta(0) <= age < timedelta(hours=24) and price >= previous_price)


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _price(value: Decimal) -> str:
    return f"¥{value:,.0f}"
