from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QPoint, QTime, Qt
from PySide6.QtGui import QColor, QPainter, QPolygon
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from ..desktop_app.airport_catalog import AirportCatalog, AirportRecord
from ..desktop_app.controller import DesktopController
from ..market import resolve_market
from ..models import EtdWindow, LegConfig, PreferredSchedule
from .widgets.airport_picker import AirportPicker
from .dashboard_page import _icon_label


class ComboDropButton(QPushButton):
    """Font-independent chevron so the drop affordance always renders."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1768d8" if self.underMouse() else "#52739f"))
        center_x, center_y = self.width() // 2, self.height() // 2
        painter.drawPolygon(QPolygon([
            QPoint(center_x - 4, center_y - 2),
            QPoint(center_x + 4, center_y - 2),
            QPoint(center_x, center_y + 3),
        ]))


class ArrowComboBox(QComboBox):
    """Combo box with an explicit drop target independent of platform styling."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.drop_button = ComboDropButton("", self, objectName="comboDropButton")
        self.drop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.drop_button.clicked.connect(self.showPopup)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        width = 32
        self.drop_button.setGeometry(self.width() - width - 1, 1, width, max(0, self.height() - 2))
        self.drop_button.raise_()


class TimeComboBox(ArrowComboBox):
    """Editable time selector with a clean drop-down instead of native spinners."""

    def __init__(self, value: QTime, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("timeCombo")
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._value = value
        for minutes in range(0, 24 * 60, 15):
            hour, minute = divmod(minutes, 60)
            self.addItem(f"{hour:02d}:{minute:02d}")
        self.addItem("23:59")
        self.setTime(value)
        self.editTextChanged.connect(self._remember_valid_time)

    def time(self) -> QTime:
        parsed = QTime.fromString(self.currentText().strip(), "H:mm")
        return parsed if parsed.isValid() else self._value

    def setTime(self, value: QTime) -> None:  # noqa: N802 - mirrors QTimeEdit
        self._value = value
        text = value.toString("HH:mm")
        index = self.findText(text)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            self.setEditText(text)

    def _remember_valid_time(self, text: str) -> None:
        parsed = QTime.fromString(text.strip(), "H:mm")
        if parsed.isValid():
            self._value = parsed


class StepperSpinBox(QFrame):
    """Spin box with themeable, larger step targets used by the route wizard."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("stepperSpin")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.editor = QSpinBox()
        self.editor.setObjectName("stepperValue")
        self.editor.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        layout.addWidget(self.editor, 1)
        controls = QVBoxLayout()
        controls.setContentsMargins(0, 2, 2, 2)
        controls.setSpacing(1)
        self.up = QPushButton("▲", objectName="spinStepButton")
        self.down = QPushButton("▼", objectName="spinStepButton")
        for button in (self.up, self.down):
            button.setFixedSize(28, 19)
            button.setAutoRepeat(True)
        self.up.clicked.connect(self.editor.stepUp)
        self.down.clicked.connect(self.editor.stepDown)
        controls.addWidget(self.up)
        controls.addWidget(self.down)
        layout.addLayout(controls)

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self.editor.setRange(minimum, maximum)

    def setValue(self, value: int) -> None:  # noqa: N802
        self.editor.setValue(value)

    def value(self) -> int:
        return self.editor.value()


class RouteWizard(QDialog):
    def __init__(
        self, catalog: AirportCatalog, controller: DesktopController, route: LegConfig | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setObjectName("routeWizard")
        self.catalog = catalog
        self.controller = controller
        self.route = route
        self.setWindowTitle("编辑航程" if route else "添加航程")
        self.setMinimumSize(940, 690)
        self.resize(1040, 760)
        self.stack = QStackedWidget()
        self.step_label = QLabel()
        self.back_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.next_button.setObjectName("primary")
        self.save_paused_button = QPushButton("保存但暂不监控")
        self.save_paused_button.hide()
        self.cancel_button = QPushButton("取消")
        self._build_fields()
        self.stack.addWidget(self._route_page())
        self.stack.addWidget(self._preferences_page())
        self.stack.addWidget(self._confirm_page())
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.save_paused_button)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(27, 23, 27, 22)
        layout.setSpacing(17)
        heading = QHBoxLayout()
        heading_copy = QVBoxLayout()
        heading_copy.addWidget(QLabel("编辑航程" if route else "添加航程", objectName="pageTitle"))
        heading_copy.addWidget(QLabel("三步完成设置，查询来源与能力边界将自动匹配", objectName="muted"))
        heading.addLayout(heading_copy)
        heading.addStretch()
        heading.addWidget(
            QLabel(
                "探索世界\n从一张好机票开始",
                objectName="brandMotto",
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
        )
        heading.addSpacing(18)
        heading.addWidget(QLabel("最多同时启用 10 条", objectName="neutralPill"))
        layout.addLayout(heading)
        self.step_labels = [QLabel() for _ in range(3)]
        step_bar = QHBoxLayout()
        step_bar.addStretch()
        for label in self.step_labels:
            step_bar.addWidget(label)
            step_bar.addSpacing(13)
        step_bar.addStretch()
        layout.addLayout(step_bar)
        self.step_label.setObjectName("muted")
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.save_paused_button.clicked.connect(lambda: self._save(force_enabled=False))
        self.origin_picker.selected_changed.connect(self._update_capability)
        self.destination_picker.selected_changed.connect(self._update_capability)
        self.trip_type.currentIndexChanged.connect(self._update_capability)
        self.direct_only.toggled.connect(self._update_layover)
        self._load_route(route)
        self._update_step()

    def _build_fields(self) -> None:
        self.origin_picker = AirportPicker(self.catalog)
        self.destination_picker = AirportPicker(self.catalog)
        self.trip_type = ArrowComboBox()
        self.trip_type.addItems(["单程", "往返"])
        self.departure_date = QDateEdit()
        self.departure_date.setMinimumDate(QDate.currentDate())
        self.departure_date.setDate(QDate.currentDate().addDays(1))
        self.departure_date.setDisplayFormat("yyyy-MM-dd")
        self.departure_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.return_date = QDateEdit()
        self.return_date.setMinimumDate(QDate.currentDate().addDays(1))
        self.return_date.setDate(QDate.currentDate().addDays(8))
        self.return_date.setDisplayFormat("yyyy-MM-dd")
        self.return_date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.departure_date.dateChanged.connect(self._sync_return_date_minimum)
        self.start_time = TimeComboBox(QTime(0, 0))
        self.end_time = TimeComboBox(QTime(23, 59))
        self.departure_period = _period_combo()
        self.return_start_time = TimeComboBox(QTime(0, 0))
        self.return_end_time = TimeComboBox(QTime(23, 59))
        self.return_period = _period_combo()
        self.direct_only = QCheckBox("只看直达航班")
        self.direct_only.setChecked(True)
        self.max_layover = StepperSpinBox()
        self.max_layover.setRange(30, 1440)
        self.max_layover.setValue(240)
        self.price = QLineEdit(placeholderText="留空表示只观察，不触发低价提醒")
        self.enabled = QCheckBox("保存后立即启用监控")
        self.enabled.setChecked(True)
        self.adult_count = StepperSpinBox()
        self.adult_count.setRange(1, 9)
        self.adult_count.setValue(1)
        self.child_count = StepperSpinBox()
        self.child_count.setRange(0, 8)
        self.cabin_class = ArrowComboBox()
        for label, value in (
            ("经济舱", "economy"),
            ("高级经济舱", "premium_economy"),
            ("商务舱", "business"),
            ("头等舱", "first"),
        ):
            self.cabin_class.addItem(label, value)
        self.focus_enabled = QCheckBox("添加重点班次（可选）")
        self.focus_label = QLineEdit(placeholderText="例如：早班直飞")
        self.focus_departure = TimeComboBox(QTime(8, 0))
        self.focus_arrival = TimeComboBox(QTime(12, 0))
        self.focus_tolerance = StepperSpinBox()
        self.focus_tolerance.setRange(0, 360)
        self.focus_tolerance.setValue(30)
        self.departure_period.currentIndexChanged.connect(
            lambda: _apply_period(self.departure_period, self.start_time, self.end_time)
        )
        self.return_period.currentIndexChanged.connect(
            lambda: _apply_period(self.return_period, self.return_start_time, self.return_end_time)
        )
        self.focus_enabled.toggled.connect(self._update_focus_fields)

    def _route_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        body = QHBoxLayout()
        body.setSpacing(15)
        fields_card = QFrame(objectName="card")
        fields_layout = QVBoxLayout(fields_card)
        fields_layout.setContentsMargins(20, 19, 20, 19)
        fields_layout.addWidget(QLabel("航线与日期", objectName="sectionTitle"))
        fields_layout.addWidget(QLabel("从内置机场目录选择具体机场，来源会自动匹配。", objectName="muted"))
        form = QFormLayout()
        form.setSpacing(12)
        # 与第 2 步一致：macOS 风格下字段默认不扩展，统一撑满宽度。
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow("从哪里出发", self.origin_picker)
        swap_row = QHBoxLayout()
        swap_row.addStretch()
        swap = QPushButton("⇅  交换起终点", objectName="swapRoute")
        swap.clicked.connect(self._swap)
        swap_row.addWidget(swap)
        form.addRow("", _layout_widget(swap_row))
        form.addRow("到哪里", self.destination_picker)
        form.addRow("航程类型", self.trip_type)
        self.domestic_roundtrip_hint = QLabel(objectName="domesticTripHint", wordWrap=True)
        self.domestic_roundtrip_hint.hide()
        form.addRow("", self.domestic_roundtrip_hint)
        self.departure_date_field = _date_field(self.departure_date, "出发", self)
        form.addRow("出发日期", self.departure_date_field)
        window = QHBoxLayout()
        window.addWidget(self.departure_period)
        window.addWidget(self.start_time)
        window.addWidget(QLabel("至"))
        window.addWidget(self.end_time)
        form.addRow("出发时间段", _layout_widget(window))
        self.return_date_label = QLabel("返程日期")
        self.return_window_label = QLabel("返程时间段")
        return_window = _time_layout(self.return_start_time, self.return_end_time)
        return_window.insertWidget(0, self.return_period)
        self.return_window_widget = _layout_widget(return_window)
        self.return_date_field = _date_field(self.return_date, "返程", self)
        form.addRow(self.return_date_label, self.return_date_field)
        form.addRow(self.return_window_label, self.return_window_widget)
        fields_layout.addLayout(form)
        fields_layout.addStretch()
        body.addWidget(fields_card, 3)
        preview = QFrame(objectName="wizardPreview")
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(18, 19, 18, 19)
        preview_layout.setSpacing(14)
        preview_layout.addWidget(QLabel("航程预览", objectName="sectionTitle"))
        self.preview_route = QLabel("选择起点与终点", objectName="routeCode", wordWrap=True)
        preview_layout.addWidget(self.preview_route)
        self.preview_places = QLabel("起运机场  →  目的机场", objectName="muted", wordWrap=True)
        preview_layout.addWidget(self.preview_places)
        self.preview_date = QLabel("待选出发日期", wordWrap=True)
        preview_layout.addWidget(self.preview_date)
        self.preview_source = QLabel("自动匹配查询来源", objectName="sourcePill", wordWrap=True)
        preview_layout.addWidget(self.preview_source)
        preview_layout.addStretch()
        preview_layout.addWidget(QLabel("支持选择全部机场或指定单一机场，按实际起降机场比价与记录。", objectName="fieldHint", wordWrap=True))
        body.addWidget(preview, 2)
        # 机场联想列表展开会推高表单；空间不足时整页滚动，
        # 避免字段被挤压或列表被父容器裁切。
        body_host = QWidget()
        body_host.setLayout(body)
        route_scroll = QScrollArea()
        route_scroll.setWidgetResizable(True)
        route_scroll.setFrameShape(QFrame.Shape.NoFrame)
        route_scroll.setStyleSheet("QScrollArea{background: transparent; border: 0;}")
        route_scroll.setWidget(body_host)
        layout.addWidget(route_scroll, 1)
        self.departure_date.dateChanged.connect(self._update_preview)
        self.trip_type.currentIndexChanged.connect(self._update_preview)
        return page

    def _preferences_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(23, 19, 23, 18)
        card_layout.setSpacing(11)
        card_layout.addWidget(QLabel("价格与班次", objectName="sectionTitle"))
        card_layout.addWidget(QLabel("选择你关心的含税价位与出行偏好，保存前仍会检查现有规则。", objectName="muted"))

        price_panel = QFrame(objectName="wizardSection")
        price_layout = QHBoxLayout(price_panel)
        price_layout.setContentsMargins(15, 10, 15, 10)
        price_copy = QVBoxLayout()
        price_copy.setSpacing(2)
        price_copy.addWidget(QLabel("含税心理价位（CNY）", objectName="formSectionLabel"))
        price_copy.addWidget(QLabel("留空表示只观察，不触发低价提醒", objectName="fieldHint"))
        price_layout.addLayout(price_copy, 2)
        price_layout.addWidget(self.price, 3)
        card_layout.addWidget(price_panel)

        flight_panel = QFrame(objectName="wizardSection")
        flight_layout = QVBoxLayout(flight_panel)
        flight_layout.setContentsMargins(15, 11, 15, 12)
        flight_layout.setSpacing(8)
        flight_title = QHBoxLayout()
        flight_title.setSpacing(5)
        flight_title.addWidget(_icon_label("plane", "#176be3", "transparent", 24))
        flight_title.addWidget(QLabel("班次设置", objectName="formSectionLabel"))
        flight_title.addStretch()
        flight_layout.addLayout(flight_title)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)
        # macOS 风格默认字段不扩展（FieldsStayAtSizeHint），控件会挤在表单中间；
        # 统一改为撑满，与设计稿一致，也保证各平台观感一致。
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow("筛选", self.direct_only)
        self.layover_label = QLabel("最长总中转等待")
        form.addRow(self.layover_label, self.max_layover)
        form.addRow("重点班次", self.focus_enabled)
        form.addRow("重点班次名称", self.focus_label)
        form.addRow("重点起飞时间", self.focus_departure)
        form.addRow("重点到达时间", self.focus_arrival)
        form.addRow("时间容差（分钟）", self.focus_tolerance)
        flight_layout.addLayout(form)
        card_layout.addWidget(flight_panel)

        passenger_panel = QFrame(objectName="wizardSection")
        passenger_layout = QGridLayout(passenger_panel)
        passenger_layout.setContentsMargins(15, 10, 15, 10)
        passenger_layout.setHorizontalSpacing(12)
        passenger_layout.addWidget(QLabel("●  乘客与舱位", objectName="formSectionLabel"), 0, 0, 1, 6)
        passenger_layout.addWidget(QLabel("成人"), 1, 0)
        passenger_layout.addWidget(self.adult_count, 1, 1)
        passenger_layout.addWidget(QLabel("儿童"), 1, 2)
        passenger_layout.addWidget(self.child_count, 1, 3)
        passenger_layout.addWidget(QLabel("舱位"), 1, 4)
        passenger_layout.addWidget(self.cabin_class, 1, 5)
        passenger_layout.setColumnStretch(1, 1)
        passenger_layout.setColumnStretch(3, 1)
        passenger_layout.setColumnStretch(5, 2)
        card_layout.addWidget(passenger_panel)

        note = QLabel("ⓘ  价格比较、历史和提醒均使用解析后的 CNY 含税总价。")
        note.setObjectName("infoStrip")
        card_layout.addWidget(note)
        # 本页自然高度超过对话框可用高度时（小屏或用户调小窗口），
        # 必须滚动而不是让 QFormLayout 被压缩成重叠的一团。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea{background: transparent; border: 0;}")
        scroll.setWidget(card)
        layout.addWidget(scroll, 1)
        return page

    def _confirm_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.summary = QLabel(wordWrap=True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(25, 22, 25, 22)
        card_layout.addWidget(QLabel("确认航程设置", objectName="sectionTitle"))
        card_layout.addWidget(self.summary)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _load_route(self, route: LegConfig | None) -> None:
        if route is None:
            self._update_capability()
            self._update_focus_fields(False)
            return
        origin_record = None
        if route.origin_airports and len(route.origin_airports) > 1:
            origin_record = self.catalog.by_city(route.origin_airport_iata)
        if origin_record is None:
            origin_record = self.catalog.by_iata(route.origin_airport_iata)
        self.origin_picker.set_record(origin_record)

        dest_record = None
        if route.destination_airports and len(route.destination_airports) > 1:
            dest_record = self.catalog.by_city(route.destination_airport_iata)
        if dest_record is None:
            dest_record = self.catalog.by_iata(route.destination_airport_iata)
        self.destination_picker.set_record(dest_record)
        self.trip_type.setCurrentIndex(1 if route.return_date else 0)
        self.departure_date.setDate(_qdate(route.departure_date))
        self.start_time.setTime(_qtime(route.etd_window.start))
        self.end_time.setTime(_qtime(route.etd_window.end))
        _select_period(self.departure_period, route.etd_window.start, route.etd_window.end)
        self.direct_only.setChecked(route.direct_only)
        self.max_layover.setValue(route.max_layover_minutes or 240)
        self.price.setText(str(route.expected_total_price_cny or ""))
        self.enabled.setChecked(route.enabled)
        self.adult_count.setValue(route.adult_count)
        self.child_count.setValue(route.child_count)
        cabin_index = self.cabin_class.findData(route.cabin_class)
        self.cabin_class.setCurrentIndex(max(0, cabin_index))
        if route.return_date and route.return_etd_window:
            self.return_date.setDate(_qdate(route.return_date))
            self.return_start_time.setTime(_qtime(route.return_etd_window.start))
            self.return_end_time.setTime(_qtime(route.return_etd_window.end))
            _select_period(
                self.return_period,
                route.return_etd_window.start,
                route.return_etd_window.end,
            )
        if route.preferred_schedules:
            focus = route.preferred_schedules[0]
            self.focus_enabled.setChecked(True)
            self.focus_label.setText(focus.label)
            self.focus_departure.setTime(_qtime(focus.departure_time))
            self.focus_arrival.setTime(_qtime(focus.arrival_time))
            self.focus_tolerance.setValue(focus.departure_tolerance_minutes)
        self._update_capability()
        self._update_focus_fields(self.focus_enabled.isChecked())

    def _update_capability(self) -> None:
        origin = self.origin_picker.selected
        destination = self.destination_picker.selected
        domestic = False
        if origin and destination:
            try:
                domestic = resolve_market(_draft_leg(origin, destination)) == "domestic"
            except ValueError:
                domestic = False
        self.trip_type.model().item(1).setEnabled(not domestic)
        if domestic:
            self.trip_type.setCurrentIndex(0)
            self.direct_only.setChecked(True)
            self.direct_only.setEnabled(False)
            return_route = f"{destination.airport_iata} → {origin.airport_iata}"
            hint = (
                "国内同程目前只支持单程直达，不支持去返程组合价。"
                f"如需监控返程，请保存去程后再添加一条 {return_route} 的反向航程，并单独选择返程日期。"
                "去程与返程各占用一条监控名额。"
            )
            self.domestic_roundtrip_hint.setText(hint)
            self.domestic_roundtrip_hint.show()
            self.trip_type.setToolTip(hint)
        else:
            self.direct_only.setEnabled(True)
            self.domestic_roundtrip_hint.hide()
            self.trip_type.setToolTip("")
        roundtrip = self.trip_type.currentIndex() == 1
        self.return_date_label.setVisible(roundtrip)
        self.return_date_field.setVisible(roundtrip)
        self.return_window_label.setVisible(roundtrip)
        self.return_window_widget.setVisible(roundtrip)
        self._update_layover()
        self._update_preview()

    def _sync_return_date_minimum(self) -> None:
        self.return_date.setMinimumDate(self.departure_date.date().addDays(1))

    def _update_preview(self) -> None:
        if not hasattr(self, "preview_route"):
            return
        origin, destination = self.origin_picker.selected, self.destination_picker.selected
        self.preview_route.setText(
            f"{origin.airport_iata if origin else '—'}  →  {destination.airport_iata if destination else '—'}"
        )
        self.preview_places.setText(
            f"{origin.city_name_zh if origin else '出发地'}  →  {destination.city_name_zh if destination else '目的地'}"
        )
        self.preview_date.setText(f"出发：{self.departure_date.date().toString('yyyy-MM-dd')}  ·  {'往返' if self.trip_type.currentIndex() else '单程'}")
        if origin and destination:
            try:
                domestic = resolve_market(_draft_leg(origin, destination)) == "domestic"
            except ValueError:
                domestic = False
            self.preview_source.setText("国内 · 同程" if domestic else "国际/跨境 · 去哪儿")
        else:
            self.preview_source.setText("选择机场后自动匹配来源")

    def _update_layover(self) -> None:
        show = not self.direct_only.isChecked() and self.direct_only.isEnabled()
        self.layover_label.setVisible(show)
        self.max_layover.setVisible(show)

    def _update_focus_fields(self, enabled: bool) -> None:
        for field in (
            self.focus_label,
            self.focus_departure,
            self.focus_arrival,
            self.focus_tolerance,
        ):
            field.setEnabled(enabled)

    def _swap(self) -> None:
        origin, destination = self.origin_picker.selected, self.destination_picker.selected
        self.origin_picker.set_record(destination)
        self.destination_picker.set_record(origin)
        self._update_capability()

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_step()

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == 0 and not self._validate_route_page():
            return
        if index == 1 and not self._validate_preferences():
            return
        if index < 2:
            if index == 1:
                self._refresh_summary()
            self.stack.setCurrentIndex(index + 1)
            self._update_step()
            return
        self._save(force_enabled=True)

    def _update_step(self) -> None:
        index = self.stack.currentIndex()
        self.step_label.setText(f"第 {index + 1} 步，共 3 步 · {'航程信息' if index == 0 else '偏好设置' if index == 1 else '确认保存'}")
        for position, label in enumerate(self.step_labels):
            label.setText(f"{position + 1}  ·  {('航程信息', '价格与班次', '确认保存')[position]}")
            label.setObjectName("stepActive" if position == index else "stepDone" if position < index else "stepPending")
            label.style().unpolish(label)
            label.style().polish(label)
        self.back_button.setVisible(index > 0)
        self.save_paused_button.setVisible(index == 2)
        if index == 2:
            self.next_button.setText("保存并开始监控")
            can_enable = (
                bool(self.route and self.route.enabled)
                or self.controller.enabled_capacity_remaining(
                    excluding_id=self.route.id if self.route else None
                ) > 0
            )
            self.next_button.setEnabled(can_enable)
            self.next_button.setToolTip(
                "" if can_enable else "当前设备已启用 10 条航程，请保存为暂停或先暂停其他航程"
            )
        else:
            self.next_button.setText("下一步")
            self.next_button.setEnabled(True)
            self.next_button.setToolTip("")

    def _validate_route_page(self) -> bool:
        if not self.origin_picker.selected or not self.destination_picker.selected:
            QMessageBox.warning(self, "请选择机场", "起运地和目的地都必须从内置机场目录中选择。")
            return False
        if self.origin_picker.selected.airport_iata == self.destination_picker.selected.airport_iata:
            QMessageBox.warning(self, "航程无效", "出发和到达机场不能相同。")
            return False
        if self.trip_type.currentIndex() == 1 and self.return_date.date() <= self.departure_date.date():
            QMessageBox.warning(self, "返程日期无效", "返程日期必须晚于出发日期。")
            return False
        return True

    def _validate_preferences(self) -> bool:
        if self.price.text().strip():
            try:
                if Decimal(self.price.text().strip()) <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                QMessageBox.warning(self, "心理价位无效", "请输入大于 0 的 CNY 金额，或留空只观察。")
                return False
        if self.enabled.isChecked() and self.controller.enabled_capacity_remaining(excluding_id=self.route.id if self.route else None) <= 0:
            self.enabled.setChecked(False)
            QMessageBox.information(self, "已达到上限", "当前设备最多同时启用 10 个航程；此航程将保存为暂停。")
        if self.focus_enabled.isChecked() and not self.focus_label.text().strip():
            QMessageBox.warning(self, "重点班次缺少名称", "请输入重点班次名称，或取消重点班次。")
            return False
        return True

    def _refresh_summary(self) -> None:
        origin = self.origin_picker.selected
        destination = self.destination_picker.selected
        assert origin and destination
        market = resolve_market(_draft_leg(origin, destination))
        source = "同程（国内）" if market == "domestic" else "去哪儿（国际/跨境）"
        kind = "往返" if self.trip_type.currentIndex() else "单程"
        return_hint = (
            f"<p style='color:#9a5a0d'>如需返程，请另建一条 "
            f"{destination.airport_iata} → {origin.airport_iata} 的反向航程，单独监控返程价格。</p>"
            if market == "domestic" else ""
        )
        self.summary.setText(
            f"<h2>{origin.display_text} → {destination.display_text}</h2>"
            f"<p>{origin.city_name_zh} → {destination.city_name_zh} · {kind}</p>"
            f"<p>出发：{self.departure_date.date().toString('yyyy-MM-dd')} · "
            f"{self.start_time.time().toString('HH:mm')}–{self.end_time.time().toString('HH:mm')}</p>"
            f"<p>来源自动匹配：<b>{source}</b></p>"
            f"{return_hint}"
            "<p>下一步请选择立即开始监控，或仅保存为暂停。</p>"
        )

    def _save(self, *, force_enabled: bool | None = None) -> None:
        try:
            self.controller.save_route(self._build_route(force_enabled=force_enabled))
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.accept()

    def _build_route(self, *, force_enabled: bool | None = None) -> LegConfig:
        origin = self.origin_picker.selected
        destination = self.destination_picker.selected
        assert origin and destination
        return_date = self.return_date.date().toPython() if self.trip_type.currentIndex() else None
        direct = self.direct_only.isChecked()
        origin_airports = origin.child_airports if origin.is_city else (origin.airport_iata,)
        destination_airports = destination.child_airports if destination.is_city else (destination.airport_iata,)
        # 城市名必须保持纯净（如"北京"）：该名字会拼进同程/去哪儿的
        # 结果页 URL（from= 参数），带"（全部）"之类后缀会让页面查询失效。
        # 全城/单机场的区分由机场码（BJS vs PEK）与 child_airports 表达。
        origin_name = origin.city_name_zh
        dest_name = destination.city_name_zh
        # 重点班次的机场过滤字段：选城市聚合时写 None（按时刻在全城航班中匹配），
        # 写城市码（如 BJS）会导致与真实机场码（PEK）永远匹配不上。
        preferred_origin_code = None if origin.is_city else origin.airport_iata
        preferred_destination_code = None if destination.is_city else destination.airport_iata
        preferred: tuple[PreferredSchedule, ...] = ()
        if self.focus_enabled.isChecked():
            preferred = (
                PreferredSchedule(
                    label=self.focus_label.text().strip(),
                    departure_time=self.focus_departure.time().toPython(),
                    arrival_time=self.focus_arrival.time().toPython(),
                    arrival_day_offset=0,
                    departure_tolerance_minutes=self.focus_tolerance.value(),
                    arrival_tolerance_minutes=self.focus_tolerance.value(),
                    origin_airport_iata=preferred_origin_code,
                    destination_airport_iata=preferred_destination_code,
                ),
            )

        return LegConfig(
            id=self.route.id if self.route else f"route-{uuid.uuid4().hex[:8]}",
            enabled=self.enabled.isChecked() if force_enabled is None else force_enabled,
            origin_airport_iata=origin.airport_iata,
            destination_airport_iata=destination.airport_iata,
            departure_date=self.departure_date.date().toPython(),
            etd_window=EtdWindow(self.start_time.time().toPython(), self.end_time.time().toPython()),
            direct_only=direct,
            expected_total_price_cny=Decimal(self.price.text().strip()) if self.price.text().strip() else None,
            top_n=10,
            adult_count=self.adult_count.value(),
            child_count=self.child_count.value(),
            cabin_class=str(self.cabin_class.currentData()),
            origin_name_zh=origin_name,
            destination_name_zh=dest_name,
            preferred_schedules=preferred,
            market="auto",
            max_layover_minutes=None if direct else self.max_layover.value(),
            return_date=return_date,
            return_etd_window=EtdWindow(self.return_start_time.time().toPython(), self.return_end_time.time().toPython()) if return_date else None,
            return_direct_only=direct if return_date else None,
            return_max_layover_minutes=(None if direct else self.max_layover.value()) if return_date else None,
            origin_airports=origin_airports,
            destination_airports=destination_airports,
        )


def _draft_leg(origin: AirportRecord, destination: AirportRecord) -> LegConfig:
    return LegConfig(
        id="draft",
        enabled=False,
        origin_airport_iata=origin.airport_iata,
        destination_airport_iata=destination.airport_iata,
        departure_date=date.today(),
        etd_window=EtdWindow(time(0, 0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=None,
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        origin_airports=origin.child_airports if origin.is_city else (origin.airport_iata,),
        destination_airports=destination.child_airports if destination.is_city else (destination.airport_iata,),
    )


def _layout_widget(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget


def _date_field(editor: QDateEdit, label: str, parent: QWidget) -> QWidget:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(editor, 1)
    button = QPushButton("▦  选日期", objectName="datePickerButton")
    button.setMinimumWidth(112)
    button.setMinimumHeight(41)
    button.setToolTip(f"打开日历选择{label}日期")
    button.clicked.connect(lambda: _open_date_picker(editor, label, parent))
    row.addWidget(button)
    return _layout_widget(row)


def _open_date_picker(editor: QDateEdit, label: str, parent: QWidget) -> None:
    dialog = QDialog(parent, objectName="datePickerDialog")
    dialog.setWindowTitle(f"选择{label}日期")
    dialog.setMinimumSize(430, 370)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 17, 20, 19)
    layout.setSpacing(12)
    layout.addWidget(QLabel(f"选择{label}日期", objectName="sectionTitle"))
    calendar = QCalendarWidget()
    calendar.setObjectName("datePickerCalendar")
    calendar.setMinimumDate(editor.minimumDate())
    calendar.setMaximumDate(editor.maximumDate())
    calendar.setSelectedDate(editor.date())
    calendar.setGridVisible(True)
    calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
    calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
    layout.addWidget(calendar, 1)
    layout.addWidget(QLabel("点击日期即可选中；也可以直接在原输入框键入日期。", objectName="fieldHint"))
    def choose(chosen: QDate) -> None:
        editor.setDate(chosen)
        dialog.accept()
    calendar.clicked.connect(choose)
    dialog.exec()


def _time_layout(start: TimeComboBox, end: TimeComboBox) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(start)
    layout.addWidget(QLabel("至"))
    layout.addWidget(end)
    return layout


def _period_combo() -> QComboBox:
    combo = ArrowComboBox()
    combo.addItem("全天", (0, 0, 23, 59))
    combo.addItem("上午", (6, 0, 12, 0))
    combo.addItem("下午", (12, 0, 18, 0))
    combo.addItem("晚上", (18, 0, 23, 59))
    combo.addItem("自定义", None)
    return combo


def _apply_period(combo: QComboBox, start: TimeComboBox, end: TimeComboBox) -> None:
    period = combo.currentData()
    if not isinstance(period, tuple):
        return
    start.setTime(QTime(period[0], period[1]))
    end.setTime(QTime(period[2], period[3]))


def _select_period(combo: QComboBox, start: time, end: time) -> None:
    expected = (start.hour, start.minute, end.hour, end.minute)
    for index in range(combo.count()):
        if combo.itemData(index) == expected:
            combo.setCurrentIndex(index)
            return
    combo.setCurrentIndex(combo.count() - 1)


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _qtime(value: time) -> QTime:
    return QTime(value.hour, value.minute)
