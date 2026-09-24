"""Daily overview page driven by persisted prices and coordinator state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import MAX_ENABLED_LEGS
from ..desktop_app.view_data import DashboardData
from ..desktop_app.route_repository import is_route_expired
from ..market import resolve_market
from ..models import LegConfig
from .aircraft_assets import aircraft_mark_pixmap
from .support import SupportPromptState


class DashboardPage(QWidget):
    def __init__(
        self,
        open_new_route: Callable[[], None],
        run_now: Callable[[], None],
        toggle_pause: Callable[[], None],
        open_routes: Callable[[], None],
        open_results: Callable[[LegConfig], None],
        open_activity: Callable[[], None] | None = None,
        open_support: Callable[[], None] | None = None,
        support_prompt_state: SupportPromptState | None = None,
    ):
        super().__init__()
        self._routes: list[LegConfig] = []
        self._data: DashboardData | None = None
        self._runtime_title = "等待配置"
        self._runtime_detail = "设置航程后可启动监控"
        self._next_run: datetime | None = None
        self._paused = False
        self._running = False
        self._open_results = open_results
        self._open_activity = open_activity
        self._open_support = open_support
        self._support_prompt_state = support_prompt_state

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget(objectName="pageHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(18)
        title_row = QHBoxLayout()
        title_row.setSpacing(13)
        title_row.addWidget(_icon_label("sun", "#f6a515", "transparent", 52), alignment=Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.setSpacing(4)
        heading.addWidget(QLabel(_greeting(), objectName="pageTitle"))
        self.runtime_detail_label = QLabel("设置航程后可启动监控", objectName="muted", wordWrap=True)
        heading.addWidget(self.runtime_detail_label)
        title_row.addLayout(heading, 1)
        title_row.setSpacing(10)
        self.pause_button = QPushButton("暂停监控", objectName="dashboardAction")
        self.pause_button.setFixedSize(112, 36)
        self.pause_button.setIcon(_plain_icon("pause", "#176be3"))
        self.pause_button.clicked.connect(toggle_pause)
        self.run_button = QPushButton("立即查询", objectName="dashboardAction")
        self.run_button.setFixedSize(116, 36)
        self.run_button.setIcon(_plain_icon("search", "#176be3"))
        self.run_button.clicked.connect(run_now)
        self.add_button = QPushButton("添加航程", objectName="dashboardPrimaryAction")
        self.add_button.setFixedSize(118, 36)
        self.add_button.setIcon(_plain_icon("plus", "#ffffff"))
        self.add_button.clicked.connect(open_new_route)
        title_row.addWidget(self.pause_button, alignment=Qt.AlignmentFlag.AlignTop)
        title_row.addWidget(self.run_button, alignment=Qt.AlignmentFlag.AlignTop)
        title_row.addWidget(self.add_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.today_card = _metric_card("tag", "今日最低价", "暂无数据", "启用航程的 CNY 含税总价", "green")
        self.count_card = _metric_card("plane", "已启用航程", "0 / 10", "严格使用单浏览器串行查询", "blue")
        self.success_card = _metric_card("clock", "最近成功", "暂无记录", "尚未取得有效完整结果", "violet")
        self.attention_card = _metric_card("warning", "需要处理", "0", "当前没有待处理航程", "amber")
        for card in (self.today_card, self.count_card, self.success_card, self.attention_card):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(16)
        routes_section = QVBoxLayout()
        route_header = QHBoxLayout()
        route_header.addWidget(QLabel("航程监控", objectName="sectionTitle"))
        route_header.addStretch()
        all_routes = QPushButton("查看全部航程  →", objectName="linkButton")
        all_routes.clicked.connect(open_routes)
        route_header.addWidget(all_routes)
        routes_section.addLayout(route_header)
        routes_section.addWidget(QLabel("关注含税价格，也能随时查看本轮全部航班候选。", objectName="muted"))
        self.route_cards = QVBoxLayout()
        self.route_cards.setSpacing(12)
        routes_section.addLayout(self.route_cards)
        routes_section.addStretch()
        body.addLayout(routes_section, 3)

        events_panel = QFrame(objectName="sidePanel")
        events_section = QVBoxLayout(events_panel)
        events_section.setContentsMargins(18, 17, 18, 18)
        activity_header = QHBoxLayout()
        activity_header.addWidget(QLabel("最近动态", objectName="sectionTitle"))
        activity_header.addStretch()
        if self._open_activity is not None:
            all_activity = QPushButton("查看全部动态  →", objectName="linkButton")
            all_activity.clicked.connect(self._open_activity)
            activity_header.addWidget(all_activity)
        events_section.addLayout(activity_header)
        events_section.addWidget(QLabel("每一次查询和需要处理的事件", objectName="muted"))
        event_list = QFrame(objectName="eventListCard")
        event_list_layout = QVBoxLayout(event_list)
        event_list_layout.setContentsMargins(12, 11, 12, 11)
        event_list_layout.setSpacing(0)
        self.event_cards = QVBoxLayout()
        self.event_cards.setSpacing(0)
        event_list_layout.addLayout(self.event_cards)
        event_list_layout.addStretch()
        events_section.addWidget(event_list, 1)
        events_section.addStretch()
        body.addWidget(events_panel, 2)
        layout.addLayout(body, 1)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        self._sync_controls()

    def refresh(self, routes: list[LegConfig]) -> None:
        self._routes = list(routes)
        enabled = sum(route.enabled for route in routes)
        _set_metric(self.count_card, f"{enabled} / {MAX_ENABLED_LEGS}", f"另有 {len(routes) - enabled} 条已暂停")
        self._render_routes()
        self._sync_controls()

    def set_data(self, data: DashboardData) -> None:
        self._data = data
        minimum_route = next(
            (route for route in self._routes if route.id == data.today_minimum_leg_id),
            None,
        )
        minimum_detail = "今日已完成查询中的最低 CNY 含税总价"
        if minimum_route is not None:
            arrow = "⇄" if minimum_route.is_round_trip else "→"
            minimum_detail = (
                f"{minimum_route.origin_airport_iata} {arrow} "
                f"{minimum_route.destination_airport_iata}"
            )
            if data.today_minimum_captured_at is not None:
                minimum_detail += f" · {_friendly_time(data.today_minimum_captured_at)}"
        _set_metric(
            self.today_card,
            _price_text(data.today_minimum_cny) if data.today_minimum_cny is not None else "暂无数据",
            minimum_detail,
        )
        _set_metric(
            self.success_card,
            _friendly_time(data.latest_success_at) if data.latest_success_at else "暂无记录",
            (
                f"上一轮耗时 {data.latest_run_duration_seconds} 秒"
                if data.latest_run_duration_seconds is not None
                else "尚未取得有效完整结果"
            ),
        )
        _set_metric(
            self.attention_card,
            str(data.attention_count),
            "请前往系统状态处理" if data.attention_count else "当前没有待处理航程",
        )
        self._render_routes()
        self._render_events()

    def set_runtime(self, title: str, detail: str) -> None:
        self._runtime_title = title
        self._runtime_detail = detail
        next_already_shown = "下次" in detail
        suffix = (
            f" · 下次查询 {_friendly_time(self._next_run)}"
            if self._next_run and not next_already_shown
            else ""
        )
        self.runtime_detail_label.setText(f"{title} · {detail}{suffix}")

    def set_runtime_message(self, message: str) -> None:
        self.set_runtime("正在准备", message)

    def set_next_run(self, due_at: datetime | None) -> None:
        self._next_run = due_at
        self.set_runtime(self._runtime_title, self._runtime_detail)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._sync_controls()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_controls()

    def _sync_controls(self) -> None:
        enabled = any(route.enabled for route in self._routes)
        self.run_button.setEnabled(enabled and not self._running and not self._paused)
        self.run_button.setText("正在查询…" if self._running else "立即查询")
        self.pause_button.setEnabled(enabled or self._paused)
        self.pause_button.setText("继续监控" if self._paused else "暂停监控")

    def _render_routes(self) -> None:
        _clear_layout(self.route_cards)
        overviews = self._data.routes if self._data else {}
        if not self._routes:
            empty = QLabel("还没有航程。点击“添加航程”，开始关注你的第一段旅程。", objectName="emptyState", wordWrap=True)
            self.route_cards.addWidget(empty)
            return
        for route in self._routes[:5]:
            overview = overviews.get(route.id)
            card = QFrame(objectName="routeCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.setSpacing(0)
            top = QHBoxLayout()
            top.setContentsMargins(18, 13, 18, 11)
            top.setSpacing(12)
            plane_icon = _icon_label("route-plane", "#176be3", "#e7f1ff", 46)
            plane_icon.setObjectName("routePlaneIcon")
            top.addWidget(plane_icon)
            route_copy = QVBoxLayout()
            route_copy.setSpacing(3)
            route_text = f"{route.origin_airport_iata}  →  {route.destination_airport_iata}"
            route_copy.addWidget(QLabel(route_text, objectName="dashboardRouteCode"))
            dates = route.departure_date.isoformat()
            if route.return_date:
                dates += f" ~ {route.return_date.isoformat()}"
            source = "国内 · 同程" if _market(route) == "domestic" else "国际/跨境 · 去哪儿"
            route_copy.addWidget(QLabel(
                f"{route.origin_name_zh or route.origin_airport_iata} → {route.destination_name_zh or route.destination_airport_iata}  ·  {dates}  ·  {source}",
                objectName="routeMeta", wordWrap=True,
            ))
            top.addLayout(route_copy, 1)
            status = _dashboard_route_status(
                route,
                overview.status if overview else None,
                overview.threshold_confirmed if overview else False,
            )
            status_style = {
                "运行中": "activePill",
                "低价命中": "lowPricePill",
            }.get(status, "pausedPill")
            top.addWidget(QLabel(status, objectName=status_style))
            top.addWidget(QLabel("•••", objectName="moreMenu"))
            card_layout.addLayout(top)
            card_layout.addWidget(_route_divider())
            bottom = QHBoxLayout()
            bottom.setContentsMargins(18, 11, 18, 13)
            bottom.setSpacing(12)
            bottom.addWidget(_icon_label("tag", "#5778a4", "transparent", 28))
            price_copy = QVBoxLayout()
            price_copy.setSpacing(1)
            price_copy.addWidget(QLabel("最低含税总价", objectName="detailLabel"))
            price = _price_text(overview.minimum_total_cny) if overview and overview.minimum_total_cny is not None else "—"
            price_copy.addWidget(QLabel(price, objectName="dashboardRoutePrice"))
            threshold = _price_text(route.expected_total_price_cny) if route.expected_total_price_cny is not None else "仅观察"
            price_copy.addWidget(QLabel(
                f"{_delta_text(overview.change_cny if overview else None)}  ·  心理价位 {threshold}",
                objectName="routeMeta",
            ))
            bottom.addLayout(price_copy, 2)
            bottom.addWidget(_icon_label("calendar", "#5778a4", "transparent", 28))
            update_copy = QVBoxLayout()
            update_copy.setSpacing(2)
            update_copy.addWidget(QLabel("更新于", objectName="detailLabel"))
            updated = _friendly_time(overview.captured_at) if overview and overview.captured_at else "—"
            update_copy.addWidget(QLabel(updated, objectName="routeUpdated"))
            bottom.addLayout(update_copy, 1)
            bottom.addStretch()
            action = QPushButton("查看航班候选  →", objectName="routeCandidateAction")
            action.clicked.connect(lambda checked=False, item=route: self._open_results(item))
            bottom.addWidget(action)
            card_layout.addLayout(bottom)
            self.route_cards.addWidget(card)
        if len(self._routes) > 5:
            self.route_cards.addWidget(QLabel(f"还有 {len(self._routes) - 5} 条航程，可在“我的航程”中查看。", objectName="muted"))

    def _render_events(self) -> None:
        _clear_layout(self.event_cards)
        events = self._data.recent_events if self._data else ()
        if not events:
            self.event_cards.addWidget(QLabel("暂无动态。完成查询后，运行记录会显示在这里。", objectName="emptyState", wordWrap=True))
            return
        if (
            self._open_support is not None
            and self._support_prompt_state is not None
            and self._support_prompt_state.should_offer(events)
        ):
            self.event_cards.addWidget(self._support_offer(events))
        for event in events[:8]:
            occurred = _parse_time(event.get("occurred_at"))
            event_type = str(event.get("event_type", ""))
            if event_type == "low_price_confirmed":
                low_price_card = self._low_price_event_card(event, occurred)
                if low_price_card is not None:
                    self.event_cards.addWidget(low_price_card)
                    continue
            kind, color, background = _event_icon(event_type, str(event.get("severity", "info")))
            entry = QFrame(objectName="timelineEntry")
            entry_layout = QHBoxLayout(entry)
            entry_layout.setContentsMargins(2, 7, 2, 7)
            entry_layout.setSpacing(10)
            entry_layout.addWidget(QLabel("●", objectName="timelineDot"), alignment=Qt.AlignmentFlag.AlignTop)
            entry_layout.addWidget(_icon_label(kind, color, background, 32), alignment=Qt.AlignmentFlag.AlignTop)
            copy = QVBoxLayout()
            copy.setSpacing(2)
            copy.addWidget(QLabel(_friendly_time(occurred) if occurred else "刚刚", objectName="timelineTime"))
            copy.addWidget(QLabel(str(event.get("message", "")), objectName="timelineMessage", wordWrap=True))
            entry_layout.addLayout(copy, 1)
            self.event_cards.addWidget(entry)

    def _low_price_event_card(
        self,
        event: dict[str, object],
        occurred: datetime | None,
    ) -> QFrame | None:
        details = event.get("details")
        if not isinstance(details, dict):
            return None
        route_code = str(details.get("route_code") or "航程")
        route_name = str(details.get("route_name") or "")
        actual = _price_text(_event_decimal(details.get("actual_price_cny")))
        threshold = _price_text(_event_decimal(details.get("threshold_price_cny")))
        savings_value = _event_decimal(details.get("savings_cny"))
        comparison = (
            f"低于心理价 {_price_text(savings_value)}"
            if savings_value is not None and savings_value > 0 else "已达到心理价"
        )

        entry = QFrame(objectName="lowPriceTimelineEntry")
        entry_layout = QHBoxLayout(entry)
        entry_layout.setContentsMargins(10, 10, 10, 9)
        entry_layout.setSpacing(9)
        entry_layout.addWidget(
            _icon_label("tag", "#14986a", "#dcf7eb", 36),
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        copy = QVBoxLayout()
        copy.setSpacing(3)
        headline = QHBoxLayout()
        headline.setSpacing(7)
        headline.addWidget(QLabel(route_code, objectName="lowPriceRoute"))
        headline.addStretch()
        headline.addWidget(QLabel(actual, objectName="lowPriceValue"))
        copy.addLayout(headline)
        if route_name:
            copy.addWidget(QLabel(f"低价命中 · {route_name}", objectName="lowPriceMeta"))
        copy.addWidget(QLabel(
            f"心理价 {threshold} · {comparison}",
            objectName="lowPriceMeta",
            wordWrap=True,
        ))
        footer = QHBoxLayout()
        footer.setSpacing(6)
        footer.addWidget(QLabel(
            _friendly_time(occurred) if occurred else "刚刚",
            objectName="timelineTime",
        ))
        footer.addStretch()
        route = next(
            (item for item in self._routes if item.id == str(event.get("leg_id") or "")),
            None,
        )
        if route is not None:
            action = QPushButton("查看候选  →", objectName="lowPriceEventAction")
            action.clicked.connect(
                lambda checked=False, item=route: self._open_results(item)
            )
            footer.addWidget(action)
        copy.addLayout(footer)
        entry_layout.addLayout(copy, 1)
        return entry

    def _support_offer(self, events: tuple[dict[str, object], ...]) -> QFrame:
        card = QFrame(objectName="supportPromptCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 11, 12, 10)
        layout.setSpacing(6)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("守到了心仪价格", objectName="supportPromptTitle"))
        heading.addStretch()
        dismiss = QPushButton("暂不需要", objectName="supportPromptDismiss")
        dismiss.setToolTip("关闭这次提示，30 天内不再提醒")
        dismiss.clicked.connect(lambda: self._dismiss_support_offer(events))
        heading.addWidget(dismiss)
        layout.addLayout(heading)
        layout.addWidget(QLabel(
            "如果航价守望帮你省下了时间，欢迎自愿支持一下。",
            objectName="supportPromptText",
            wordWrap=True,
        ))
        support = QPushButton("支持一下  →", objectName="supportPromptAction")
        support.clicked.connect(lambda: self._open_support_offer(events))
        layout.addWidget(support, alignment=Qt.AlignmentFlag.AlignLeft)
        return card

    def _dismiss_support_offer(self, events: tuple[dict[str, object], ...]) -> None:
        if self._support_prompt_state is not None:
            self._support_prompt_state.dismiss(events)
        self._render_events()

    def _open_support_offer(self, events: tuple[dict[str, object], ...]) -> None:
        if self._support_prompt_state is not None:
            self._support_prompt_state.dismiss(events)
        self._render_events()
        if self._open_support is not None:
            self._open_support()


def _metric_card(icon: str, title: str, value: str, detail: str, accent: str) -> QFrame:
    card = QFrame(objectName="metricCard")
    card.setProperty("accent", accent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(17, 14, 17, 14)
    layout.setSpacing(5)
    icon_row = QHBoxLayout()
    tones = {
        "green": ("#21a875", "#ddf6eb"),
        "blue": ("#176be3", "#e4efff"),
        "violet": ("#7956db", "#eee9ff"),
        "amber": ("#e99316", "#fff0d7"),
    }
    color, background = tones[accent]
    icon_row.addWidget(_icon_label(icon, color, background, 34))
    icon_row.addWidget(QLabel(title, objectName="metricTitle"))
    icon_row.addStretch()
    layout.addLayout(icon_row)
    value_label = QLabel(value, objectName="metricValue")
    layout.addWidget(value_label)
    layout.addWidget(QLabel(detail, objectName="detail", wordWrap=True))
    return card


def _icon_label(kind: str, color: str, background: str, size: int) -> QLabel:
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(_icon_pixmap(kind, color, background, size))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _plain_icon(kind: str, color: str) -> QIcon:
    return QIcon(_icon_pixmap(kind, color, "transparent", 22))


def _icon_pixmap(kind: str, color: str, background: str, size: int) -> QPixmap:
    if kind in {"plane", "route-plane"}:
        return aircraft_mark_pixmap(
            size,
            background=background,
            circular_background=kind == "route-plane",
        )
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / 32.0
    painter.scale(scale, scale)
    if background != "transparent":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(QRectF(1, 1, 30, 30), 10, 10)
    stroke = QColor(color)
    painter.setPen(QPen(stroke, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "sun":
        painter.setBrush(stroke)
        painter.drawEllipse(QRectF(11, 11, 10, 10))
        for start, end in (
            ((16, 4), (16, 8)), ((16, 24), (16, 28)), ((4, 16), (8, 16)), ((24, 16), (28, 16)),
            ((7.5, 7.5), (10, 10)), ((22, 22), (24.5, 24.5)), ((24.5, 7.5), (22, 10)), ((10, 22), (7.5, 24.5)),
        ):
            painter.drawLine(QPointF(*start), QPointF(*end))
    elif kind == "tag":
        path = QPainterPath(QPointF(7, 14))
        path.lineTo(15, 6)
        path.lineTo(25, 7)
        path.lineTo(26, 17)
        path.lineTo(18, 25)
        path.closeSubpath()
        painter.setBrush(stroke)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(20.5, 11.5), 2, 2)
    elif kind == "clock":
        painter.drawEllipse(QRectF(7, 7, 18, 18))
        painter.drawLine(QPointF(16, 10), QPointF(16, 17))
        painter.drawLine(QPointF(16, 17), QPointF(21, 20))
    elif kind == "warning":
        path = QPainterPath(QPointF(16, 5))
        path.lineTo(27, 26)
        path.lineTo(5, 26)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(16, 12), QPointF(16, 19))
        painter.setBrush(stroke)
        painter.drawEllipse(QPointF(16, 22.5), 1.2, 1.2)
    elif kind == "search":
        painter.drawEllipse(QRectF(7, 7, 13, 13))
        painter.drawLine(QPointF(18.5, 18.5), QPointF(25, 25))
    elif kind == "pause":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(stroke)
        painter.drawRoundedRect(QRectF(9, 7, 5, 18), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(18, 7, 5, 18), 1.5, 1.5)
    elif kind == "plus":
        painter.drawLine(QPointF(16, 7), QPointF(16, 25))
        painter.drawLine(QPointF(7, 16), QPointF(25, 16))
    elif kind == "calendar":
        painter.drawRoundedRect(QRectF(7, 9, 18, 17), 2, 2)
        painter.drawLine(QPointF(7, 14), QPointF(25, 14))
        painter.drawLine(QPointF(11, 6), QPointF(11, 11))
        painter.drawLine(QPointF(21, 6), QPointF(21, 11))
    elif kind == "chart":
        painter.drawLine(QPointF(6, 6), QPointF(6, 26))
        painter.drawLine(QPointF(6, 26), QPointF(27, 26))
        painter.drawPolyline(QPolygonF([QPointF(9, 21), QPointF(14, 15), QPointF(18, 18), QPointF(26, 8)]))
    elif kind == "folder":
        path = QPainterPath(QPointF(5, 10))
        path.lineTo(13, 10)
        path.lineTo(16, 7)
        path.lineTo(27, 7)
        path.lineTo(27, 24)
        path.lineTo(5, 24)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "settings":
        painter.drawEllipse(QRectF(10, 10, 12, 12))
        painter.drawEllipse(QRectF(14, 14, 4, 4))
        for start, end in (
            ((16, 5), (16, 9)), ((16, 23), (16, 27)),
            ((5, 16), (9, 16)), ((23, 16), (27, 16)),
            ((8.2, 8.2), (11, 11)), ((21, 21), (23.8, 23.8)),
            ((23.8, 8.2), (21, 11)), ((11, 21), (8.2, 23.8)),
        ):
            painter.drawLine(QPointF(*start), QPointF(*end))
    elif kind == "database":
        painter.drawEllipse(QRectF(7, 6, 18, 7))
        painter.drawArc(QRectF(7, 11, 18, 7), 0, -180 * 16)
        painter.drawArc(QRectF(7, 16, 18, 7), 0, -180 * 16)
        painter.drawLine(QPointF(7, 9.5), QPointF(7, 21))
        painter.drawLine(QPointF(25, 9.5), QPointF(25, 21))
        painter.drawArc(QRectF(7, 18, 18, 7), 180 * 16, 180 * 16)
    elif kind == "status":
        painter.drawEllipse(QRectF(6, 6, 20, 20))
        painter.drawEllipse(QRectF(10, 10, 12, 12))
        painter.setBrush(stroke)
        painter.drawEllipse(QRectF(14, 14, 4, 4))
    elif kind == "desktop":
        painter.drawRoundedRect(QRectF(6, 7, 20, 15), 2, 2)
        painter.drawLine(QPointF(13, 26), QPointF(19, 26))
        painter.drawLine(QPointF(16, 22), QPointF(16, 26))
    elif kind == "bell":
        path = QPainterPath(QPointF(8, 22))
        path.lineTo(10, 19)
        path.lineTo(10, 13)
        path.quadTo(10, 8, 16, 8)
        path.quadTo(22, 8, 22, 13)
        path.lineTo(22, 19)
        path.lineTo(24, 22)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(QRectF(13, 21, 6, 5), 0, -180 * 16)
    elif kind == "power":
        painter.drawArc(QRectF(7, 7, 18, 19), 45 * 16, 270 * 16)
        painter.drawLine(QPointF(16, 4), QPointF(16, 16))
    elif kind == "link":
        painter.drawArc(QRectF(5, 11, 13, 10), 55 * 16, 250 * 16)
        painter.drawArc(QRectF(14, 11, 13, 10), 235 * 16, 250 * 16)
        painter.drawLine(QPointF(12, 16), QPointF(20, 16))
    elif kind == "cube":
        painter.drawPolygon(QPolygonF([QPointF(16, 5), QPointF(26, 10), QPointF(16, 15), QPointF(6, 10)]))
        painter.drawPolyline(QPolygonF([QPointF(6, 10), QPointF(6, 21), QPointF(16, 27), QPointF(16, 15)]))
        painter.drawPolyline(QPolygonF([QPointF(26, 10), QPointF(26, 21), QPointF(16, 27)]))
    elif kind == "refresh":
        painter.drawArc(QRectF(7, 7, 18, 18), 35 * 16, 275 * 16)
        painter.drawPolyline(QPolygonF([QPointF(22, 7), QPointF(25, 7), QPointF(25, 11)]))
    elif kind == "save":
        painter.drawRoundedRect(QRectF(7, 5, 18, 22), 2, 2)
        painter.drawRect(QRectF(11, 6, 10, 7))
        painter.drawRoundedRect(QRectF(11, 18, 10, 7), 1, 1)
    elif kind in {"up", "down"}:
        if kind == "down":
            painter.drawLine(QPointF(16, 7), QPointF(16, 24))
            painter.drawPolyline(QPolygonF([QPointF(9, 17), QPointF(16, 24), QPointF(23, 17)]))
        else:
            painter.drawLine(QPointF(16, 25), QPointF(16, 8))
            painter.drawPolyline(QPolygonF([QPointF(9, 15), QPointF(16, 8), QPointF(23, 15)]))
    elif kind == "document":
        painter.drawRoundedRect(QRectF(9, 6, 14, 21), 2, 2)
        painter.drawLine(QPointF(13, 12), QPointF(20, 12))
        painter.drawLine(QPointF(13, 17), QPointF(20, 17))
        painter.drawLine(QPointF(13, 22), QPointF(18, 22))
    else:
        painter.drawEllipse(QRectF(6, 6, 20, 20))
        painter.drawPolyline(QPolygonF([QPointF(10, 16), QPointF(14, 20), QPointF(22, 11)]))
    painter.end()
    return pixmap


def _route_divider() -> QFrame:
    divider = QFrame(objectName="routeDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    return divider


def _event_icon(event_type: str, severity: str) -> tuple[str, str, str]:
    if event_type == "cycle_finished":
        return "check", "#1ca875", "#e2f7ee"
    if event_type == "cycle_started":
        return "search", "#176be3", "#e6f0ff"
    if event_type in {"routes_changed", "settings_changed"}:
        return "document", "#176be3", "#e6f0ff"
    if event_type == "routes_expired":
        return "clock", "#e69218", "#fff1d9"
    if event_type == "low_price_confirmed":
        return "tag", "#176be3", "#e6f0ff"
    if severity in {"warning", "error"}:
        return "warning", "#e69218", "#fff1d9"
    return "clock", "#5f789e", "#edf2f8"


def _dashboard_route_status(
    route: LegConfig,
    status: str | None,
    threshold_confirmed: bool = False,
) -> str:
    if is_route_expired(route):
        return "已过期"
    if not route.enabled:
        return "已暂停"
    if status == "manual_attention":
        return "需要处理"
    if status == "failed":
        return "查询失败"
    if threshold_confirmed:
        return "低价命中"
    return "运行中"


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.deleteLater()


def _set_metric(card: QFrame, value: str, detail: str) -> None:
    card.findChild(QLabel, "metricValue").setText(value)
    card.findChild(QLabel, "detail").setText(detail)


def _price_text(value: Decimal | None) -> str:
    return "—" if value is None else f"¥{value:,.0f}"


def _event_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _delta_text(value: Decimal | None) -> str:
    if value is None:
        return "暂无对比"
    if value == 0:
        return "较上次持平"
    direction = "上涨" if value > 0 else "下降"
    return f"较上次{direction} ¥{abs(value):,.0f}"


def _friendly_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%m-%d %H:%M")


def _greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if hour < 6:
        return "夜深了，旅行家"
    if hour < 12:
        return "早上好，旅行家"
    if hour < 18:
        return "下午好，旅行家"
    return "晚上好，旅行家"


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _status_text(status: str) -> str:
    return {
        "success": "查询成功",
        "failed": "查询失败",
        "manual_attention": "需要人工处理",
    }.get(status, status)


def _market(route: LegConfig) -> str:
    try:
        return resolve_market(route)
    except ValueError:
        return route.market
