"""Reusable, non-technical runtime preference controls."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..desktop_app.browser_detector import BrowserCandidate, BrowserDetector
from ..desktop_app.settings_repository import DesktopSettings
from .dashboard_page import _icon_label, _plain_icon


class RuntimePreferencesForm(QWidget):
    redetect_requested = Signal()

    def __init__(
        self,
        browsers: list[BrowserCandidate],
        settings: DesktopSettings,
        *,
        show_redetect: bool = True,
        show_launch_to_tray: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._initial = settings
        self.setObjectName("runtimePreferencesForm")
        self.browser_combo = QComboBox(objectName="runtimeBrowserCombo")
        self.interval_combo = QComboBox(objectName="runtimeIntervalCombo")
        for minutes in (30, 60, 120):
            self.interval_combo.addItem(f"{minutes} 分钟", minutes)
        self.show_browser = QCheckBox("显示浏览器运行过程", objectName="runtimeOption")
        self.desktop_notifications = QCheckBox("开启桌面通知", objectName="runtimeOption")
        self.autostart = QCheckBox("登录系统后自动启动", objectName="runtimeOption")
        self.launch_to_tray = QCheckBox("仅驻留菜单栏，隐藏 Dock 图标", objectName="runtimeOption")
        self.browser_status = QLabel(objectName="muted", wordWrap=True)

        browser_row = QHBoxLayout()
        browser_row.setSpacing(12)
        browser_row.addWidget(self.browser_combo, 1)
        self.redetect_button: QPushButton | None = None
        if show_redetect:
            self.redetect_button = QPushButton("重新检测", objectName="redetectButton")
            self.redetect_button.setIcon(_plain_icon("refresh", "#176be3"))
            self.redetect_button.clicked.connect(self.redetect_requested.emit)
            browser_row.addWidget(self.redetect_button)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        form.setColumnStretch(2, 1)
        # onboarding 不暴露托盘模式：新用户首启就“应用消失只剩菜单栏图标”
        # 容易被当成崩溃；该开关留给系统状态页。
        rows = [
            ("link", "用于查询的浏览器", _layout_widget(browser_row)),
            ("clock", "自动查询间隔", self.interval_combo),
            ("desktop", "浏览器窗口", self.show_browser),
            ("bell", "价格提醒", self.desktop_notifications),
            ("power", "开机启动", self.autostart),
        ]
        if show_launch_to_tray:
            rows.append(("status", "托盘模式", self.launch_to_tray))
        for row, (kind, title, control) in enumerate(rows):
            form.addWidget(_icon_label(kind, "#4b70a3", "transparent", 26), row, 0)
            form.addWidget(QLabel(title, objectName="runtimeFieldLabel"), row, 1)
            form.addWidget(control, row, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.browser_status)
        self.set_browsers(browsers, preferred_path=settings.browser_path, preferred_kind=settings.browser_kind)
        self.load(settings)

    @property
    def has_browser(self) -> bool:
        return isinstance(self.browser_combo.currentData(), BrowserCandidate)

    def set_browsers(
        self,
        browsers: list[BrowserCandidate],
        *,
        preferred_path: str | None = None,
        preferred_kind: str = "auto",
    ) -> None:
        self.browser_combo.clear()
        selected_index = -1
        preferred = BrowserDetector.select(
            browsers,
            preferred_kind=preferred_kind,
            preferred_path=preferred_path,
        )
        for index, candidate in enumerate(browsers):
            version = f" · {candidate.version}" if candidate.version else ""
            name = "Google Chrome" if candidate.kind == "chrome" else "Microsoft Edge"
            self.browser_combo.addItem(f"{name}{version}", candidate)
            if candidate == preferred:
                selected_index = index
        if not browsers:
            self.browser_combo.addItem("未检测到 Chrome 或 Edge", None)
            self.browser_combo.setEnabled(False)
            self.browser_status.setText("需要先安装 Chrome 或 Edge，航价守望不会使用你的日常浏览器数据。")
        else:
            self.browser_combo.setEnabled(True)
            self.browser_combo.setCurrentIndex(max(0, selected_index))
            self.browser_status.setText("查询会使用航价守望自己的独立浏览器空间，不读取日常浏览记录。")

    def load(self, settings: DesktopSettings) -> None:
        self._initial = settings
        index = self.interval_combo.findData(settings.interval_minutes)
        self.interval_combo.setCurrentIndex(max(0, index))
        self.show_browser.setChecked(settings.show_browser)
        self.desktop_notifications.setChecked(settings.desktop_notifications)
        self.autostart.setChecked(settings.autostart)
        self.launch_to_tray.setChecked(settings.launch_to_tray)

    def values(self, *, onboarding_completed: bool | None = None) -> DesktopSettings:
        candidate = self.browser_combo.currentData()
        completed = self._initial.onboarding_completed if onboarding_completed is None else onboarding_completed
        return replace(
            self._initial,
            browser_kind=candidate.kind if isinstance(candidate, BrowserCandidate) else "auto",
            browser_path=str(candidate.path) if isinstance(candidate, BrowserCandidate) else None,
            interval_minutes=int(self.interval_combo.currentData()),
            show_browser=self.show_browser.isChecked(),
            desktop_notifications=self.desktop_notifications.isChecked(),
            autostart=self.autostart.isChecked(),
            launch_to_tray=self.launch_to_tray.isChecked(),
            onboarding_completed=completed,
        )


def preference_card(title: str, subtitle: str, form: QWidget) -> QFrame:
    card = QFrame(objectName="systemSettingsCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(24, 18, 24, 20)
    layout.setSpacing(12)
    heading_row = QHBoxLayout()
    heading_row.setSpacing(12)
    heading_row.addWidget(_icon_label("settings", "#2778eb", "#e8f2ff", 44))
    heading_copy = QVBoxLayout()
    heading_copy.setSpacing(2)
    heading_copy.addWidget(QLabel(title, objectName="systemSectionTitle"))
    heading_copy.addWidget(QLabel(subtitle, objectName="muted", wordWrap=True))
    heading_row.addLayout(heading_copy, 1)
    layout.addLayout(heading_row)
    layout.addSpacing(2)
    layout.addWidget(form)
    return card


def _layout_widget(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget
