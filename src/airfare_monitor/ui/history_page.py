"""Lightweight persisted CNY total-price history page."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..models import LegConfig
from ..storage import SQLiteStore
from .dashboard_page import _icon_label, _plain_icon


class PriceChart(QWidget):
    def __init__(self):
        super().__init__()
        self.rows: list[dict[str, object]] = []
        self.threshold: Decimal | None = None
        self._hit_points: list[tuple[int, QPointF, dict[str, object], Decimal | None]] = []
        self._hovered_index: int | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(310)

    def set_series(self, rows: list[dict[str, object]], threshold: Decimal | None) -> None:
        self.rows = list(rows)
        self.threshold = threshold
        self._hit_points = []
        self._hovered_index = None
        QToolTip.hideText()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        hit = self._nearest_hit(event.position())
        if hit is None:
            if self._hovered_index is not None:
                self._hovered_index = None
                QToolTip.hideText()
                self.update()
            return

        index, _, row, value = hit
        if self._hovered_index != index:
            self._hovered_index = index
            QToolTip.showText(
                event.globalPosition().toPoint(),
                _chart_tooltip_text(row, value),
                self,
                self.rect(),
                30_000,
            )
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._hovered_index = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def _nearest_hit(
        self, position: QPointF, *, radius: float = 10.0,
    ) -> tuple[int, QPointF, dict[str, object], Decimal | None] | None:
        candidates = [
            (point.x() - position.x()) ** 2 + (point.y() - position.y()) ** 2
            for _, point, _, _ in self._hit_points
        ]
        if not candidates:
            return None
        nearest = min(range(len(candidates)), key=candidates.__getitem__)
        return self._hit_points[nearest] if candidates[nearest] <= radius * radius else None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        area = self.rect().adjusted(62, 22, -24, -42)
        valid = [
            _decimal(row.get("minimum_total_price_cny"))
            for row in self.rows
            if str(row.get("status")) == "success"
            and _decimal(row.get("minimum_total_price_cny")) is not None
        ]
        values = [value for value in valid if value is not None]
        if not values:
            painter.setPen(QColor("#7287a6"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "完成查询后将在这里显示价格曲线")
            return
        if self.threshold is not None:
            values.append(self.threshold)

        low, high = min(values), max(values)
        padding = max(Decimal("20"), (high - low) * Decimal("0.12"))
        low -= padding
        high += padding
        low, high, tick_step = _nice_axis_bounds(low, high)
        span = high - low or Decimal("1")
        grid_lines = max(2, int(round(span / tick_step)) + 1)
        painter.setPen(QPen(QColor("#e8eef6"), 1))
        for step in range(grid_lines):
            fraction = Decimal(step) / Decimal(grid_lines - 1)
            y = area.top() + area.height() * float(fraction)
            painter.drawLine(area.left(), int(y), area.right(), int(y))
            price = high - span * fraction
            label = f"{price:,.0f}"
            painter.setPen(QColor("#7287a6"))
            painter.drawText(QRectF(0, y - 9, 55, 18), Qt.AlignmentFlag.AlignRight, label)
            painter.setPen(QPen(QColor("#e8eef6"), 1))
        for step in range(1, 5):
            x = area.left() + area.width() * step / 5
            painter.drawLine(int(x), area.top(), int(x), area.bottom())

        def point(index: int, value: Decimal) -> QPointF:
            denominator = max(1, len(self.rows) - 1)
            x = area.left() + area.width() * index / denominator
            y = area.bottom() - float((value - low) / span) * area.height()
            return QPointF(x, y)

        self._hit_points = []

        if self.threshold is not None:
            threshold_y = point(0, self.threshold).y()
            painter.setPen(QPen(QColor("#ee8a15"), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(area.left(), int(threshold_y), area.right(), int(threshold_y))
            painter.drawText(
                QRectF(area.right() - 155, threshold_y - 23, 150, 20),
                Qt.AlignmentFlag.AlignRight,
                f"心理价位 ¥{self.threshold:,.0f}",
            )

        painter.setPen(QPen(QColor("#176fe4"), 3))
        for segment in price_segments(self.rows):
            path = QPainterPath()
            for position, (index, value) in enumerate(segment):
                current = point(index, value)
                if position == 0:
                    path.moveTo(current)
                else:
                    path.lineTo(current)
            fill_path = QPainterPath(path)
            if segment:
                first_point = point(segment[0][0], segment[0][1])
                last_point = point(segment[-1][0], segment[-1][1])
                fill_path.lineTo(last_point.x(), area.bottom())
                fill_path.lineTo(first_point.x(), area.bottom())
                fill_path.closeSubpath()
                gradient = QLinearGradient(0, area.top(), 0, area.bottom())
                gradient.setColorAt(0, QColor(23, 111, 228, 48))
                gradient.setColorAt(1, QColor(23, 111, 228, 3))
                painter.fillPath(fill_path, gradient)
            painter.setPen(QPen(QColor("#176fe4"), 3))
            painter.drawPath(path)
            for index, value in segment:
                current = point(index, value)
                self._hit_points.append((index, current, self.rows[index], value))
                painter.setPen(QPen(QColor("#176fe4"), 3))
                painter.setBrush(QColor("#ffffff"))
                radius = 6 if index == self._hovered_index else 4
                painter.drawEllipse(current, radius, radius)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e14c4c"))
        for index, row in enumerate(self.rows):
            if str(row.get("status")) != "success" or _decimal(row.get("minimum_total_price_cny")) is None:
                x = point(index, low).x()
                current = QPointF(x, area.bottom())
                self._hit_points.append((index, current, row, None))
                radius = 6 if index == self._hovered_index else 4.5
                painter.drawEllipse(current, radius, radius)

        first_time = _datetime(self.rows[0].get("captured_at")) if self.rows else None
        last_time = _datetime(self.rows[-1].get("captured_at")) if self.rows else None
        painter.setPen(QColor("#7287a6"))
        metrics = QFontMetrics(painter.font())
        if first_time:
            painter.drawText(area.left(), area.bottom() + 27, first_time.strftime("%m-%d %H:%M"))
        if last_time:
            label = last_time.strftime("%m-%d %H:%M")
            painter.drawText(area.right() - metrics.horizontalAdvance(label), area.bottom() + 27, label)


class HistoryPage(QWidget):
    def __init__(
        self,
        store: SQLiteStore | None,
        *,
        open_latest_report: Callable[[], None],
        outputs_dir: Path | None,
        open_activity: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.setObjectName("pageCanvas")
        self.store = store
        self.open_latest_report = open_latest_report
        self.outputs_dir = outputs_dir
        self.open_activity = open_activity
        self.routes: list[LegConfig] = []
        self.hours = 24

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(17)
        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title_row.addWidget(_icon_label("chart", "#176be3", "transparent", 54), alignment=Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.addWidget(QLabel("历史价格", objectName="pageTitle"))
        heading.addWidget(QLabel("查看航程在不同时间的 CNY 含税总价变化", objectName="muted"))
        title_row.addLayout(heading)
        title_row.addStretch()
        report_button = QPushButton("打开最新 Excel", objectName="historyAction")
        report_button.setIcon(_plain_icon("document", "#176be3"))
        report_button.clicked.connect(open_latest_report)
        folder_button = QPushButton("打开报告目录", objectName="historyAction")
        folder_button.setIcon(_plain_icon("folder", "#176be3"))
        folder_button.clicked.connect(self.open_output_directory)
        title_row.addWidget(report_button)
        title_row.addWidget(folder_button)
        layout.addLayout(title_row)

        filters = QHBoxLayout()
        filters.setSpacing(14)
        route_selector = QFrame(objectName="historyRouteSelector")
        route_selector_layout = QHBoxLayout(route_selector)
        route_selector_layout.setContentsMargins(15, 5, 12, 5)
        route_selector_layout.setSpacing(10)
        route_selector_layout.addWidget(_icon_label("plane", "#176be3", "transparent", 32))
        self.route_combo = QComboBox()
        self.route_combo.setObjectName("historyRouteCombo")
        self.route_combo.currentIndexChanged.connect(self.reload)
        route_selector_layout.addWidget(self.route_combo, 1)
        filters.addWidget(route_selector, 1)
        period_switch = QFrame(objectName="historyPeriodSwitch")
        period_layout = QHBoxLayout(period_switch)
        period_layout.setContentsMargins(2, 2, 2, 2)
        period_layout.setSpacing(2)
        self.period_group = QButtonGroup(self)
        for label, hours in (("最近 24 小时", 24), ("最近 7 天", 24 * 7)):
            button = QPushButton(label, objectName="periodButton", checkable=True)
            button.setProperty("hours", hours)
            button.clicked.connect(lambda checked=False, value=hours: self.set_period(value))
            self.period_group.addButton(button)
            period_layout.addWidget(button)
            if hours == 24:
                button.setChecked(True)
        filters.addWidget(period_switch)
        layout.addLayout(filters)

        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.current_card = _history_metric("tag", "当前含税价", "—", "尚无有效价格", "blue")
        self.minimum_card = _history_metric("down", "区间最低", "—", "尚无有效价格", "green")
        self.maximum_card = _history_metric("up", "区间最高", "—", "尚无有效价格", "amber")
        for card in (self.current_card, self.minimum_card, self.maximum_card):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        body = QHBoxLayout()
        chart_card = QFrame(objectName="featureCard")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(18, 16, 18, 15)
        chart_header = QHBoxLayout()
        chart_header.addWidget(QLabel("含税总价（CNY）", objectName="sectionTitle"))
        chart_header.addStretch()
        self.period_summary = QLabel("▣  最近 24 小时", objectName="periodSummary")
        chart_header.addWidget(self.period_summary)
        chart_layout.addLayout(chart_header)
        legend = QHBoxLayout()
        legend.setSpacing(18)
        legend.addWidget(QLabel("●  有效价格", objectName="legendValid"))
        legend.addWidget(QLabel("●  未获得有效价格", objectName="legendFailed"))
        legend.addWidget(QLabel("┄  心理价位", objectName="legendThreshold"))
        legend.addStretch()
        chart_layout.addLayout(legend)
        self.chart = PriceChart()
        chart_layout.addWidget(self.chart, 1)
        self.chart_note = QLabel("蓝线为有效价格；红点表示查询失败或未取得有效价格。", objectName="muted")
        chart_layout.addWidget(self.chart_note)
        body.addWidget(chart_card, 3)

        records_card = QFrame(objectName="featureCard")
        records_layout = QVBoxLayout(records_card)
        records_layout.setContentsMargins(16, 16, 16, 15)
        records_header = QHBoxLayout()
        records_header.addWidget(QLabel("最近查询记录", objectName="sectionTitle"))
        records_header.addStretch()
        if self.open_activity is not None:
            more = QPushButton("查看更多  →", objectName="linkButton")
            more.clicked.connect(self.open_activity)
            records_header.addWidget(more)
        records_layout.addLayout(records_header)
        self.records = QTableWidget(0, 3)
        self.records.setObjectName("historyRecords")
        self.records.setHorizontalHeaderLabels(["", "时间", "结果"])
        self.records.horizontalHeader().setStretchLastSection(True)
        self.records.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.records.setColumnWidth(0, 30)
        self.records.verticalHeader().setVisible(False)
        self.records.verticalHeader().setDefaultSectionSize(34)
        self.records.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        records_layout.addWidget(self.records)
        body.addWidget(records_card, 2)
        layout.addLayout(body, 1)

    def refresh(self, routes: list[LegConfig]) -> None:
        selected_id = self.route_combo.currentData()
        self.routes = list(routes)
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        for route in routes:
            label = f"{route.origin_name_zh or route.origin_airport_iata} {route.origin_airport_iata} → {route.destination_name_zh or route.destination_airport_iata} {route.destination_airport_iata}"
            self.route_combo.addItem(label, route.id)
        if selected_id:
            index = self.route_combo.findData(selected_id)
            self.route_combo.setCurrentIndex(max(0, index))
        self.route_combo.blockSignals(False)
        self.reload()

    def set_period(self, hours: int) -> None:
        self.hours = hours
        self.period_summary.setText("▣  最近 24 小时" if hours == 24 else "▣  最近 7 天")
        self.reload()

    def reload(self) -> None:
        leg_id = self.route_combo.currentData()
        route = next((item for item in self.routes if item.id == leg_id), None)
        if route is None:
            rows: list[dict[str, object]] = []
        else:
            rows = self.store.leg_price_series(
                route.id,
                since=datetime.now() - timedelta(hours=self.hours),
                max_points=500,
            ) if self.store is not None else []
        threshold = route.expected_total_price_cny if route else None
        self.chart.set_series(rows, threshold)
        valid_rows = [
            (row, value)
            for row in rows
            if str(row.get("status")) == "success"
            and (value := _decimal(row.get("minimum_total_price_cny"))) is not None
        ]
        if valid_rows:
            _, current_value = valid_rows[-1]
            previous_value = valid_rows[-2][1] if len(valid_rows) > 1 else None
            current_detail = _comparison_detail(current_value, previous_value)
            minimum_row, minimum_value = min(valid_rows, key=lambda item: item[1])
            maximum_row, maximum_value = max(valid_rows, key=lambda item: item[1])
            _set_history_metric(self.current_card, _price(current_value), current_detail)
            _set_history_metric(self.minimum_card, _price(minimum_value), _occurred_detail(minimum_row))
            _set_history_metric(self.maximum_card, _price(maximum_value), _occurred_detail(maximum_row))
        else:
            for card in (self.current_card, self.minimum_card, self.maximum_card):
                _set_history_metric(card, "—", "尚无有效价格")
        recent = list(reversed(rows[-12:]))
        self.records.setRowCount(len(recent))
        for index, row in enumerate(recent):
            captured = _datetime(row.get("captured_at"))
            value = _decimal(row.get("minimum_total_price_cny"))
            status = str(row.get("status"))
            result = _price(value) if status == "success" and value is not None else _history_status(status)
            marker = QTableWidgetItem("●")
            marker.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            marker.setForeground(QColor("#176fe4") if status == "success" and value is not None else QColor("#e14c4c"))
            self.records.setItem(index, 0, marker)
            self.records.setItem(index, 1, QTableWidgetItem(captured.strftime("%m-%d %H:%M") if captured else "—"))
            self.records.setItem(index, 2, QTableWidgetItem(result))
        self.records.resizeColumnToContents(1)

    def open_output_directory(self) -> None:
        if self.outputs_dir is None or not self.outputs_dir.is_dir():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.outputs_dir.resolve())))


def price_segments(rows: list[dict[str, object]]) -> list[list[tuple[int, Decimal]]]:
    """Split valid points at failures so the graph never implies a valid bridge."""
    segments: list[list[tuple[int, Decimal]]] = []
    current: list[tuple[int, Decimal]] = []
    for index, row in enumerate(rows):
        value = _decimal(row.get("minimum_total_price_cny"))
        if str(row.get("status")) == "success" and value is not None:
            current.append((index, value))
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def _history_metric(icon: str, title: str, value: str, detail: str, accent: str) -> QFrame:
    card = QFrame(objectName="metricCard")
    card.setProperty("accent", accent)
    layout = QHBoxLayout(card)
    layout.setContentsMargins(18, 15, 18, 15)
    layout.setSpacing(13)
    tones = {
        "blue": ("#176be3", "#e4efff"),
        "green": ("#18a66f", "#dcf6e9"),
        "amber": ("#ed891e", "#fff0d9"),
    }
    color, background = tones[accent]
    layout.addWidget(_icon_label(icon, color, background, 48), alignment=Qt.AlignmentFlag.AlignTop)
    copy = QVBoxLayout()
    copy.setSpacing(3)
    copy.addWidget(QLabel(title, objectName="metricTitle"))
    copy.addWidget(QLabel(value, objectName="metricValue"))
    copy.addWidget(QLabel(detail, objectName="metricDetail"))
    layout.addLayout(copy, 1)
    return card


def _set_history_metric(card: QFrame, value: str, detail: str) -> None:
    card.findChild(QLabel, "metricValue").setText(value)
    card.findChild(QLabel, "metricDetail").setText(detail)


def _comparison_detail(current: Decimal, previous: Decimal | None) -> str:
    if previous is None or previous == 0:
        return "首次取得有效价格"
    change = (current - previous) / previous * Decimal("100")
    if change == 0:
        return "较上次查询 0%  ·  价格保持稳定"
    direction = "上涨" if change > 0 else "下降"
    return f"较上次查询 {abs(change):.1f}%  ·  价格{direction}"


def _occurred_detail(row: dict[str, object]) -> str:
    occurred = _datetime(row.get("captured_at"))
    return f"出现在 {occurred.strftime('%m-%d %H:%M')}" if occurred else "时间未知"


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _nice_axis_bounds(low: Decimal, high: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """把绘图范围外扩到整数刻度，让 Y 轴标签落在可读的整数上。"""
    lo, hi = float(low), float(high)
    raw_step = max((hi - lo) / 4, 1.0)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(
        multiplier * magnitude
        for multiplier in (1, 2, 2.5, 5, 10)
        if multiplier * magnitude >= raw_step
    )
    axis_low = math.floor(lo / step) * step
    axis_high = math.ceil(hi / step) * step
    if axis_high <= axis_low:
        axis_high = axis_low + step
    return Decimal(str(axis_low)), Decimal(str(axis_high)), Decimal(str(step))


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _price(value: Decimal) -> str:
    return f"¥{value:,.0f}"


def _history_status(status: str) -> str:
    return {
        "failed": "查询失败",
        "manual_attention": "需要人工处理",
        "success": "无符合条件航班",
    }.get(status, "未取得价格")


def _chart_tooltip_text(row: dict[str, object], value: Decimal | None) -> str:
    captured = _datetime(row.get("captured_at"))
    captured_text = captured.strftime("%Y-%m-%d %H:%M:%S") if captured else "时间未知"
    if str(row.get("status")) == "success" and value is not None:
        return f"采集时间：{captured_text}\n含税总价：{_price(value)}"
    return f"采集时间：{captured_text}\n查询结果：{_history_status(str(row.get('status')))}"
