from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...desktop_app.airport_catalog import AirportCatalog, AirportRecord


class AirportPicker(QWidget):
    selected_changed = Signal(object)

    MAX_VISIBLE_ROWS = 5

    def __init__(self, catalog: AirportCatalog, parent: QWidget | None = None):
        super().__init__(parent)
        self.catalog = catalog
        self.selected: AirportRecord | None = None
        self.input = QLineEdit(placeholderText="搜索机场、城市或 IATA")
        # 联想列表内联在输入框下方：把后续字段往下推，不遮挡任何内容。
        # 高度按内容固定（最多 5 行），布局必须为其保留确切空间，
        # 不会再被父布局挤压裁切；页面空间不足时由滚动容器兜底。
        self.results = QListWidget()
        self.results.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.input)
        layout.addWidget(self.results)
        self.input.textChanged.connect(self._search)
        self.results.itemClicked.connect(self._select_item)
        self._search("")

    def set_record(self, record: AirportRecord | None) -> None:
        self.selected = record
        if record is not None:
            self.input.setText(record.display_text)
            self.results.hide()
        else:
            self.input.clear()

    def _search(self, text: str) -> None:
        if self.selected is not None and text != self.selected.display_text:
            self.selected = None
            self.selected_changed.emit(None)
        self.results.clear()
        for record in self.catalog.search(text, include_cities=True):
            item = QListWidgetItem(f"{record.display_text}  ·  {record.airport_name_zh or record.city_name_zh}")
            item.setData(256, record)
            self.results.addItem(item)
        if self.results.count() == 0 or not self.input.hasFocus():
            self.results.hide()
            return
        row_height = max(self.results.sizeHintForRow(0), 26)
        self.results.setFixedHeight(row_height * min(self.results.count(), self.MAX_VISIBLE_ROWS) + 10)
        self.results.show()

    def _select_item(self, item: QListWidgetItem) -> None:
        record = item.data(256)
        self.selected = record
        self.input.blockSignals(True)
        self.input.setText(record.display_text)
        self.input.blockSignals(False)
        self.results.hide()
        self.selected_changed.emit(record)
