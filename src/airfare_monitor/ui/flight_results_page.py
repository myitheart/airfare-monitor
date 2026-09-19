"""Latest complete flight candidates with local filtering and comparison."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..market import resolve_market
from ..models import LegConfig
from ..storage import SQLiteStore


class FlightResultsPage(QWidget):
    def __init__(
        self,
        store: SQLiteStore | None,
        *,
        on_back: Callable[[], None],
        on_open_search: Callable[[LegConfig], None] | None = None,
    ):
        super().__init__()
        self.setObjectName("pageCanvas")
        self.store = store
        self.on_back = on_back
        self.on_open_search = on_open_search
        self.route: LegConfig | None = None
        self.result: dict[str, object] | None = None
        self.candidates: list[dict[str, object]] = []
        self.selected: set[str] = set()
        self._candidate_by_id: dict[str, dict[str, object]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 27, 30, 27)
        layout.setSpacing(16)

        title_row = QHBoxLayout()
        back = QPushButton("← 返回我的航程", objectName="linkButton")
        back.clicked.connect(on_back)
        title_row.addWidget(back)
        heading = QVBoxLayout()
        self.title = QLabel("航班候选", objectName="pageTitle")
        self.subtitle = QLabel("查看最近一次完整查询中符合当前条件的航班", objectName="muted")
        heading.addWidget(self.title)
        heading.addWidget(self.subtitle)
        title_row.addLayout(heading)
        title_row.addStretch()
        self.complete_badge = QLabel("完整响应", objectName="activePill")
        title_row.addWidget(self.complete_badge)
        self.open_site_button = QPushButton("在来源网站打开", objectName="historyAction")
        self.open_site_button.setToolTip("用默认浏览器打开与监控同口径的来源网站搜索结果页")
        self.open_site_button.hide()
        if self.on_open_search is not None:
            self.open_site_button.clicked.connect(self._open_search)
            title_row.addWidget(self.open_site_button)
        layout.addLayout(title_row)

        self.notice = QLabel(objectName="warningText", wordWrap=True)
        self.notice.hide()
        layout.addWidget(self.notice)

        metrics = QHBoxLayout()
        self.minimum_card = _metric_card("最低含税总价", "—", "CNY", "green")
        self.eligible_card = _metric_card("符合条件", "0", "本轮候选", "blue")
        self.direct_card = _metric_card("行程构成", "—", "直达 / 中转", "violet")
        self.time_card = _metric_card("查询时间", "—", "最近完整结果", "amber")
        for card in (self.minimum_card, self.eligible_card, self.direct_card, self.time_card):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        filters = QFrame(objectName="toolbarCard")
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(14, 10, 14, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索航班号或航司代码")
        self.search.textChanged.connect(self._render)
        filter_layout.addWidget(self.search, 2)
        self.sort_combo = QComboBox()
        for label, value in (
            ("价格最低", "price"),
            ("起飞最早", "departure"),
            ("到达最早", "arrival"),
            ("飞行时间最短", "duration"),
        ):
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self._render)
        filter_layout.addWidget(self.sort_combo)
        self.connection_combo = QComboBox()
        self.connection_combo.addItem("全部行程", "all")
        self.connection_combo.addItem("仅直达", "direct")
        self.connection_combo.addItem("仅中转", "transfer")
        self.connection_combo.currentIndexChanged.connect(self._render)
        filter_layout.addWidget(self.connection_combo)
        self.period_combo = QComboBox()
        self.period_combo.addItem("全部起飞时段", "all")
        self.period_combo.addItem("上午 06—12", "morning")
        self.period_combo.addItem("下午 12—18", "afternoon")
        self.period_combo.addItem("晚上 18—24", "evening")
        self.period_combo.addItem("凌晨 00—06", "night")
        self.period_combo.currentIndexChanged.connect(self._render)
        filter_layout.addWidget(self.period_combo)
        self.below_threshold = QCheckBox("低于心理价位")
        self.below_threshold.toggled.connect(self._render)
        filter_layout.addWidget(self.below_threshold)
        layout.addWidget(filters)

        count_row = QHBoxLayout()
        count_row.addWidget(QLabel("本轮符合条件候选", objectName="sectionTitle"))
        self.result_count = QLabel("0 条", objectName="muted")
        count_row.addWidget(self.result_count)
        count_row.addStretch()
        count_row.addWidget(QLabel("价格均为查询时观察到的 CNY 含税总价", objectName="muted"))
        layout.addLayout(count_row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["对比", "排名 / 航班", "去程时刻", "行程", "含税总价", "票价 / 行李 / 余票", "返程"]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(54)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(88)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setWordWrap(True)
        layout.addWidget(self.table, 1)

        self.compare_bar = QFrame(objectName="compareBar")
        compare_layout = QHBoxLayout(self.compare_bar)
        self.compare_label = QLabel("尚未选择航班")
        compare_layout.addWidget(self.compare_label)
        compare_layout.addStretch()
        clear = QPushButton("清空选择")
        clear.clicked.connect(self._clear_selection)
        self.compare_button = QPushButton("对比已选航班", objectName="primary")
        self.compare_button.clicked.connect(self._open_comparison)
        compare_layout.addWidget(clear)
        compare_layout.addWidget(self.compare_button)
        layout.addWidget(self.compare_bar)
        self._update_compare_bar()

    def show_route(self, route: LegConfig) -> None:
        self.route = route
        self.selected.clear()
        if self.on_open_search is not None:
            self.open_site_button.setText(f"在{'同程' if _market(route) == 'domestic' else '去哪儿'}打开")
            self.open_site_button.show()
        arrow = "⇄" if route.return_date else "→"
        origin = route.origin_name_zh or route.origin_airport_iata
        destination = route.destination_name_zh or route.destination_airport_iata
        self.title.setText(
            f"{origin} {route.origin_airport_iata} {arrow} {destination} {route.destination_airport_iata}"
        )
        source = "国内 · 同程" if _market(route) == "domestic" else "国际/跨境 · 去哪儿"
        dates = route.departure_date.isoformat()
        if route.return_date:
            dates += f" — {route.return_date.isoformat()}"
        self.subtitle.setText(f"{dates} · {source} · 当前航程设置范围内的候选")
        self.result = self.store.latest_flight_candidates(route.id) if self.store else None
        self.candidates = list(self.result.get("flights", [])) if self.result else []
        self._candidate_by_id = {
            str(item.get("flight_signature")): item for item in self.candidates
        }
        self.below_threshold.setEnabled(route.expected_total_price_cny is not None)
        if route.expected_total_price_cny is None:
            self.below_threshold.setChecked(False)
        self._render_summary()
        self._render()
        self._update_compare_bar()

    def _open_search(self) -> None:
        if self.route is not None and self.on_open_search is not None:
            self.on_open_search(self.route)

    def _render_summary(self) -> None:
        if not self.result:
            _set_metric(self.minimum_card, "—", "尚无完整查询")
            _set_metric(self.eligible_card, "0", "请先执行一次查询")
            _set_metric(self.direct_card, "—", "直达 / 中转")
            _set_metric(self.time_card, "—", "最近完整结果")
            self.complete_badge.setText("暂无结果")
            self.complete_badge.setObjectName("pausedPill")
            self.notice.setText("当前航程还没有完整查询结果。完成一次查询后，这里会展示候选航班。")
            self.notice.show()
            return

        self.complete_badge.setText("完整响应")
        self.complete_badge.setObjectName("activePill")
        prices = [_decimal(item.get("total_price_cny")) for item in self.candidates]
        valid_prices = [value for value in prices if value is not None]
        minimum = min(valid_prices) if valid_prices else None
        eligible = int(self.result.get("eligible_count") or 0)
        stored = int(self.result.get("stored_count") or 0)
        direct = sum(bool(item.get("is_direct")) for item in self.candidates)
        transfer = stored - direct
        captured = _datetime(self.result.get("captured_at"))
        _set_metric(self.minimum_card, _money(minimum), "CNY 含税总价")
        _set_metric(self.eligible_card, str(eligible), f"本地展示 {stored} 条")
        _set_metric(self.direct_card, f"{direct} / {transfer}", "直达 / 中转")
        _set_metric(self.time_card, captured.strftime("%m-%d %H:%M") if captured else "—", "最近完整结果")

        notice_parts: list[str] = []
        latest = self.store.latest_leg_results([self.route.id]) if self.store and self.route else []
        if latest and str(latest[0].get("run_id")) != str(self.result.get("run_id")):
            notice_parts.append("最近一次查询未成功，当前展示的是上一次完整查询结果。")
        if eligible > stored:
            notice_parts.append(f"本轮共有 {eligible} 个符合条件候选，当前保存并展示最低价前 {stored} 个。")
        if notice_parts:
            self.notice.setText(" ".join(notice_parts))
            self.notice.show()
        else:
            self.notice.hide()

    def _render(self, *_args: object) -> None:
        route = self.route
        rows = filter_and_sort_candidates(
            self.candidates,
            search=self.search.text(),
            connection=str(self.connection_combo.currentData()),
            period=str(self.period_combo.currentData()),
            below_threshold=(route.expected_total_price_cny if route and self.below_threshold.isChecked() else None),
            sort_by=str(self.sort_combo.currentData()),
        )
        self.result_count.setText(f"{len(rows)} 条")
        self.table.setRowCount(len(rows))
        for row_index, candidate in enumerate(rows):
            signature = str(candidate.get("flight_signature"))
            checkbox = QCheckBox()
            checkbox.setChecked(signature in self.selected)
            checkbox.toggled.connect(
                lambda checked, key=signature, control=checkbox: self._toggle_candidate(key, checked, control)
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(checkbox, alignment=Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row_index, 0, holder)
            rank = candidate.get("rank_number") or row_index + 1
            self.table.setItem(row_index, 1, _item(f"#{rank}\n{_flight_codes(candidate)}"))
            self.table.setItem(row_index, 2, _item(_outbound_times(candidate)))
            self.table.setItem(row_index, 3, _item(_journey(candidate)))
            price_item = _item(_money(_decimal(candidate.get("total_price_cny"))), center=True)
            price_item.setForeground(QColor("#1265db"))
            bold = QFont(price_item.font())
            bold.setBold(True)
            price_item.setFont(bold)
            self.table.setItem(row_index, 4, price_item)
            self.table.setItem(
                row_index,
                5,
                _item(f"{_fare_breakdown(candidate)}\n{_baggage_and_seats(candidate)}"),
            )
            self.table.setItem(row_index, 6, _item(_return_summary(candidate)))

    def _toggle_candidate(self, signature: str, checked: bool, checkbox: QCheckBox) -> None:
        if checked and signature not in self.selected and len(self.selected) >= 3:
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
            QMessageBox.information(self, "最多选择 3 个", "一次最多对比 3 个航班候选。")
            return
        if checked:
            self.selected.add(signature)
        else:
            self.selected.discard(signature)
        self._update_compare_bar()

    def _clear_selection(self) -> None:
        self.selected.clear()
        self._render()
        self._update_compare_bar()

    def _update_compare_bar(self) -> None:
        count = len(self.selected)
        self.compare_label.setText(f"已选择 {count} / 3 个航班")
        self.compare_button.setEnabled(count >= 2)

    def _open_comparison(self) -> None:
        candidates = [
            self._candidate_by_id[key]
            for key in self.selected
            if key in self._candidate_by_id
        ]
        if len(candidates) < 2:
            return
        FlightComparisonDialog(candidates, self).exec()


class FlightComparisonDialog(QDialog):
    def __init__(self, candidates: list[dict[str, object]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("comparisonDialog")
        self.setWindowTitle("对比航班")
        self.resize(920, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("航班对比", objectName="pageTitle"))
        layout.addWidget(QLabel("价格均为同一轮查询时观察到的 CNY 含税总价", objectName="muted"))
        fields = (
            ("含税总价", lambda item: _money(_decimal(item.get("total_price_cny")))),
            ("航班", _flight_codes),
            ("去程时刻", _outbound_times),
            ("总耗时", lambda item: _duration(item.get("duration_minutes"))),
            ("行程", _journey),
            ("基础票价 / 税费", _fare_breakdown),
            ("行李 / 余票", _baggage_and_seats),
            ("返程", _return_summary),
        )
        table = QTableWidget(len(fields), len(candidates) + 1)
        table.setHorizontalHeaderLabels(["对比项"] + [_flight_codes(item) for item in candidates])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        for row, (label, formatter) in enumerate(fields):
            table.setItem(row, 0, _item(label))
            for column, candidate in enumerate(candidates, start=1):
                table.setItem(row, column, _item(formatter(candidate)))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setDefaultSectionSize(50)
        layout.addWidget(table, 1)
        close = QPushButton("关闭", objectName="primary")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


def filter_and_sort_candidates(
    candidates: list[dict[str, object]],
    *,
    search: str = "",
    connection: str = "all",
    period: str = "all",
    below_threshold: Decimal | None = None,
    sort_by: str = "price",
) -> list[dict[str, object]]:
    query = search.strip().upper()
    result: list[dict[str, object]] = []
    for item in candidates:
        codes = " ".join(str(value) for value in item.get("flight_codes", []))
        carriers = " ".join(str(value) for value in item.get("carrier_codes", []))
        if query and query not in f"{codes} {carriers}".upper():
            continue
        is_direct = bool(item.get("is_direct"))
        if connection == "direct" and not is_direct:
            continue
        if connection == "transfer" and is_direct:
            continue
        departure = _datetime(item.get("etd_local"))
        if departure and period != "all" and not _period_matches(departure.hour, period):
            continue
        total = _decimal(item.get("total_price_cny"))
        if below_threshold is not None and (total is None or total > below_threshold):
            continue
        result.append(item)

    key = {
        "departure": lambda item: _datetime(item.get("etd_local")) or datetime.max,
        "arrival": lambda item: _datetime(item.get("eta_local")) or datetime.max,
        "duration": lambda item: int(item.get("duration_minutes") or 10**9),
        "price": lambda item: _decimal(item.get("total_price_cny")) or Decimal("Infinity"),
    }.get(sort_by)
    return sorted(result, key=key or (lambda item: int(item.get("rank_number") or 10**9)))


def _period_matches(hour: int, period: str) -> bool:
    return {
        "morning": 6 <= hour < 12,
        "afternoon": 12 <= hour < 18,
        "evening": 18 <= hour < 24,
        "night": 0 <= hour < 6,
    }.get(period, True)


def _metric_card(title: str, value: str, detail: str, accent: str) -> QFrame:
    card = QFrame(objectName="metricCard")
    card.setProperty("accent", accent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(17, 14, 17, 14)
    layout.addWidget(QLabel(title, objectName="muted"))
    layout.addWidget(QLabel(value, objectName="metricValue"))
    layout.addWidget(QLabel(detail, objectName="detail"))
    return card


def _set_metric(card: QFrame, value: str, detail: str) -> None:
    card.findChild(QLabel, "metricValue").setText(value)
    card.findChild(QLabel, "detail").setText(detail)


def _item(value: str, *, center: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    if center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def _flight_codes(item: dict[str, object]) -> str:
    values = item.get("flight_codes") or []
    return " / ".join(str(value) for value in values) or "—"


def _outbound_times(item: dict[str, object]) -> str:
    departure = _datetime(item.get("etd_local"))
    arrival = _datetime(item.get("eta_local"))
    if not departure or not arrival:
        return "—"
    day_offset = (arrival.date() - departure.date()).days
    suffix = f" +{day_offset}天" if day_offset else ""
    origin = str(item.get("origin_airport_iata") or "")
    destination = str(item.get("destination_airport_iata") or "")
    return f"{origin} {departure:%H:%M} → {arrival:%H:%M}{suffix} {destination}"


def _journey(item: dict[str, object]) -> str:
    duration = _duration(item.get("duration_minutes"))
    if bool(item.get("is_direct")):
        return f"直达 · {duration}"
    connections = " / ".join(str(value) for value in item.get("connection_airports", [])) or "未知机场"
    layover = _duration(item.get("layover_minutes"))
    return f"中转 {connections}\n等待 {layover} · 总程 {duration}"


def _fare_breakdown(item: dict[str, object]) -> str:
    base = _decimal(item.get("base_price_cny"))
    tax = _decimal(item.get("tax_cny"))
    base_text = f"基础票价 {_money(base)}" if base is not None else "基础票价未提供"
    tax_text = f"税费 {_money(tax)}" if tax is not None else "税费未提供"
    return f"{base_text}\n{tax_text}"


def _baggage_and_seats(item: dict[str, object]) -> str:
    tiers = _luggage_price_tiers(item)
    baggage = "\n".join(tiers) if tiers else "行李额未提供"
    seats = item.get("seat_availability")
    remaining = item.get("remaining_seats")
    seat_text = _seat_text(seats) or (f"余票 {remaining}" if remaining else "余票未提供")
    return f"{baggage}\n{seat_text}"


def _luggage_price_tiers(item: dict[str, object]) -> list[str]:
    """按「价格级」呈现行李信息：本档免费额 + 含行李最低价（若接口提供）。

    数据边界：列表响应只含免费额与含行李最低价两个信号；按重量的
    分档购买价属详情页数据，本工具不做逐航班详情抓取。
    """
    pieces = item.get("free_baggage_piece")
    weight = str(item.get("free_baggage_weight") or "").strip()
    weight = "" if weight in ("0", "0kg", "0KG", "0Kg") else weight
    piece_count = int(pieces) if pieces not in (None, "") else 0
    luggage_price = _decimal(item.get("luggage_inclusive_price_cny"))
    if piece_count == 0 and not weight and luggage_price is None and pieces is None:
        return []  # 接口完全未提供行李信息
    tiers: list[str] = []
    if piece_count > 0 or weight:
        free_text = f"本价含免费托运 {piece_count or '—'} 件"
        if weight:
            free_text += f" / {weight}"
        tiers.append(free_text)
    else:
        tiers.append("本价不含免费托运行李")
    luggage_price = _decimal(item.get("luggage_inclusive_price_cny"))
    total = _decimal(item.get("total_price_cny"))
    if luggage_price is not None and total is not None:
        delta = luggage_price - total
        suffix = f"（+¥{delta:,.0f}）" if delta > 0 else ""
        tiers.append(f"含行李最低档 ¥{luggage_price:,.0f}{suffix}")
    return tiers


def _seat_text(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    count = value.get("count_hint")
    text = value.get("count_text")
    scarcity = value.get("scarcity_text")
    if count is not None:
        result = "余票 9 张或以上" if int(count) >= 9 else f"余票 {count} 张"
    elif text:
        result = str(text)
    else:
        return None
    if scarcity and str(scarcity) not in result:
        result += f" · {scarcity}"
    return result


def _return_summary(item: dict[str, object]) -> str:
    inbound = item.get("return_itinerary")
    if not isinstance(inbound, dict):
        return "单程"
    codes = " / ".join(str(value) for value in inbound.get("flight_codes", [])) or "—"
    departure = _datetime(inbound.get("etd_local"))
    arrival = _datetime(inbound.get("eta_local"))
    times = f"{departure:%m-%d %H:%M} → {arrival:%m-%d %H:%M}" if departure and arrival else ""
    direct = "直达" if inbound.get("is_direct") else "中转"
    return f"{codes}\n{times}\n{direct}"


def _duration(value: object) -> str:
    try:
        minutes = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return "—"
    if minutes <= 0:
        return "—"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}小时{remainder}分" if remainder else f"{hours}小时"


def _money(value: Decimal | None) -> str:
    return "—" if value is None else f"¥{value:,.0f}"


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _market(route: LegConfig) -> str:
    try:
        return resolve_market(route)
    except ValueError:
        return route.market
