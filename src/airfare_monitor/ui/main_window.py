from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QFileDialog, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..config import MAX_ENABLED_LEGS
from ..desktop_app.airport_catalog import AirportCatalog
from ..desktop_app.browser_detector import BrowserCandidate
from ..desktop_app.controller import DesktopController
from ..desktop_app.events import (
    CoordinatorStateChanged, CycleFinished, CycleStarted, FatalError, LegFinished, LegStarted,
    ManualAttentionRequested, NextRunScheduled, MailDeliveryFailed,
    VerificationBrowserOpened,
)
from ..desktop_app.preferences import PreferencesManager
from ..desktop_app.mail_profile import MailProfileRepository
from ..desktop_app.diagnostics import export_diagnostic_zip
from ..desktop_app.view_data import load_dashboard_data
from ..models import LegConfig
from ..search_link import search_site_label
from ..storage import SQLiteStore
from .dashboard_page import DashboardPage, _icon_label, _plain_icon
from .flight_results_page import FlightResultsPage
from .history_page import HistoryPage
from .preferences import RuntimePreferencesForm, preference_card
from .notifications_page import NotificationsPage
from .route_wizard import RouteWizard
from .app_icon import application_icon
from .aircraft_assets import aircraft_mark_pixmap
from .theme import AviationPageStack
from .support import SupportDialog, SupportPromptState, locate_support_assets
from .. import __version__


def _nav_icon(kind: str) -> QIcon:
    """Create aligned two-state vector icons instead of relying on font glyph metrics."""

    icon = QIcon()
    for color, mode, state in (
        ("#5c7394", QIcon.Mode.Normal, QIcon.State.Off),
        ("#176be3", QIcon.Mode.Active, QIcon.State.Off),
        ("#176be3", QIcon.Mode.Normal, QIcon.State.On),
        ("#176be3", QIcon.Mode.Active, QIcon.State.On),
    ):
        icon.addPixmap(_nav_pixmap(kind, QColor(color)), mode, state)
    return icon


def _nav_pixmap(kind: str, color: QColor) -> QPixmap:
    if kind == "plane":
        return aircraft_mark_pixmap(24)
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "home":
        painter.drawPolyline(QPolygonF([QPointF(4, 11), QPointF(12, 4), QPointF(20, 11)]))
        painter.drawRoundedRect(QRectF(6.5, 10, 11, 9.5), 1.5, 1.5)
    elif kind == "history":
        painter.drawLine(QPointF(4, 4), QPointF(4, 20))
        painter.drawLine(QPointF(4, 20), QPointF(21, 20))
        painter.drawPolyline(QPolygonF([QPointF(6, 16), QPointF(10, 12), QPointF(14, 14), QPointF(20, 7)]))
    elif kind == "bell":
        path = QPainterPath(QPointF(6, 16))
        path.lineTo(8, 13)
        path.lineTo(8, 9)
        path.quadTo(8, 4.5, 12, 4.5)
        path.quadTo(16, 4.5, 16, 9)
        path.lineTo(16, 13)
        path.lineTo(18, 16)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(QRectF(9.5, 16, 5, 4), 0, -180 * 16)
    else:
        painter.drawEllipse(QRectF(6, 6, 12, 12))
        painter.drawEllipse(QRectF(10, 10, 4, 4))
        for start, end in (
            ((12, 2.5), (12, 5)), ((12, 19), (12, 21.5)),
            ((2.5, 12), (5, 12)), ((19, 12), (21.5, 12)),
            ((5.3, 5.3), (7, 7)), ((17, 17), (18.7, 18.7)),
            ((18.7, 5.3), (17, 7)), ((7, 17), (5.3, 18.7)),
        ):
            painter.drawLine(QPointF(*start), QPointF(*end))
    painter.end()
    return pixmap


class MainWindow(QMainWindow):
    runtime_status_changed = Signal(str)
    monitor_paused_changed = Signal(bool)
    runtime_settings_saved = Signal(object)

    def __init__(
        self,
        controller: DesktopController,
        catalog: AirportCatalog,
        browsers: list[BrowserCandidate],
        preferences: PreferencesManager,
        on_run_now: Callable[[], bool | None] | None = None,
        on_pause: Callable[[], bool | None] | None = None,
        on_resume: Callable[[], bool | None] | None = None,
        on_retry_leg: Callable[[str], bool | None] | None = None,
        on_open_verification: Callable[[str], bool | None] | None = None,
        on_open_search: Callable[[LegConfig], None] | None = None,
        history_store: SQLiteStore | None = None,
        outputs_dir: Path | None = None,
    ):
        super().__init__()
        self.controller = controller
        self.catalog = catalog
        self.browsers = browsers
        self.preferences = preferences
        self.history_store = history_store
        self.outputs_dir = outputs_dir
        self.latest_report_path: Path | None = None
        self._latest_prices: dict[str, Decimal] = {}
        self._support_assets = locate_support_assets()
        self._support_prompt_state = SupportPromptState(
            self.preferences.repository.user_root / "data" / "support-prompt.json"
        )
        self.setWindowTitle("航价守望")
        self.setMinimumSize(1100, 720)
        self.resize(1370, 860)
        self.on_run_now = on_run_now
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_retry_leg = on_retry_leg
        self.on_open_verification = on_open_verification
        self.on_open_search = on_open_search
        self._paused = False
        self._build()
        self.controller.on_routes_changed(self.refresh_routes)
        self.refresh_routes(self.controller.current_routes())
        self.refresh_from_history()

    def _build(self) -> None:
        root = QWidget(objectName="workspaceRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar = self._make_sidebar()
        layout.addWidget(self.sidebar)
        self.pages = AviationPageStack()
        self.dashboard = DashboardPage(
            self._open_new_route,
            self._run_now,
            self._toggle_pause,
            lambda: self._switch_page(1),
            self._open_results,
            lambda: self._switch_page(4),
            open_support=self._open_support if self._support_assets.available else None,
            support_prompt_state=self._support_prompt_state,
        )
        self.routes_page = RoutesPage(
            self.controller, self.catalog, open_results=self._open_results, on_open_search=self._open_search_link
        )
        self.history = HistoryPage(
            self.history_store,
            open_latest_report=self.open_latest_report,
            outputs_dir=self.outputs_dir,
            open_activity=lambda: self._switch_page(4),
        )
        self.notifications = NotificationsPage(
            self.preferences,
            MailProfileRepository(
                self.preferences.repository.path,
                user_root=self.preferences.repository.user_root,
            ),
        )
        self.notifications.settings_saved.connect(
            lambda: self.runtime_settings_saved.emit(None)
        )
        self.system = SystemStatusPage(
            self.browsers,
            self.preferences,
            history_store=self.history_store,
            outputs_dir=self.outputs_dir,
            retry_leg=self._retry_leg,
            open_verification=self._open_verification,
        )
        self.flight_results = FlightResultsPage(
            self.history_store,
            on_back=lambda: self._switch_page(1),
            on_open_search=self._open_search_link,
        )
        self.system.settings_saved.connect(self.runtime_settings_saved.emit)
        for page in (
            self.dashboard,
            self.routes_page,
            self.history,
            self.notifications,
            self.system,
            self.flight_results,
        ):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self._configure_status_bar()

    def _configure_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setObjectName("appStatusBar")
        bar.setSizeGripEnabled(False)
        host = QWidget(objectName="statusHost")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(6)
        self._status_dot = QLabel("●", objectName="statusBarDot")
        self._status_dot.setProperty("tone", "idle")
        self._status_text = QLabel("就绪", objectName="statusBarText")
        self._status_meta = QLabel("", objectName="statusBarMeta")
        row.addWidget(self._status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._status_text, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._status_meta, 1, Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(host, 1)
        self._set_status_message("就绪", "")

    def _set_status_message(self, title: str, detail: str = "") -> None:
        tone = "idle"
        if title in {"正在查询", "正在准备"}:
            tone = "busy"
        elif title in {"本轮完成", "最近完成"}:
            tone = "ok"
        elif title in {"需要人工处理", "等待配置", "已暂停"}:
            tone = "warn"
        elif title in {"运行异常"}:
            tone = "error"
        self._status_dot.setProperty("tone", tone)
        self._status_dot.style().unpolish(self._status_dot)
        self._status_dot.style().polish(self._status_dot)
        self._status_text.setText(title)
        self._status_meta.setText(f"·  {detail}" if detail else "")
        self.statusBar().clearMessage()

    def _make_sidebar(self) -> QWidget:
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(264)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 28, 16, 22)
        layout.setSpacing(6)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(application_icon().pixmap(50, 50))
        brand_row.addWidget(brand_icon)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand_copy.addWidget(QLabel("航价守望", objectName="brand"))
        brand_copy.addWidget(QLabel("让更好的旅程发生", objectName="tagline"))
        brand_row.addLayout(brand_copy, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(38)
        self.nav_buttons: list[QPushButton] = []
        for index, (icon_kind, text) in enumerate((("home", "概览"), ("plane", "我的航程"), ("history", "历史价格"), ("bell", "通知设置"), ("settings", "系统状态"))):
            button = QPushButton(text, objectName="navButton", checkable=True)
            button.setIcon(_nav_icon(icon_kind))
            button.setIconSize(QSize(22, 22))
            button.setMinimumHeight(52)
            button.clicked.connect(lambda checked=False, i=index: self._switch_page(i))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch()
        layout.addWidget(QLabel("关注价格，也关注更大的世界。", objectName="sidebarNote", alignment=Qt.AlignmentFlag.AlignCenter))
        layout.addWidget(QLabel("个人工具  ·  同时最多 10 条", objectName="sidebarNote", alignment=Qt.AlignmentFlag.AlignCenter))
        if self._support_assets.available:
            support_button = QPushButton("支持一下", objectName="sidebarSupport")
            support_button.setToolTip("完全自愿，不影响任何功能")
            support_button.clicked.connect(self._open_support)
            layout.addWidget(support_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(QLabel(f"v{__version__}", objectName="sidebarVersion", alignment=Qt.AlignmentFlag.AlignCenter))
        return sidebar

    def _open_support(self) -> None:
        if not self._support_assets.available:
            return
        SupportDialog(self._support_assets, self).exec()

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)

    def _open_new_route(self) -> None:
        wizard = RouteWizard(self.catalog, self.controller, parent=self)
        wizard.exec()

    def _open_results(self, route: LegConfig) -> None:
        self.flight_results.show_route(route)
        self._switch_page(5)

    def _open_search_link(self, route: LegConfig) -> None:
        if self.on_open_search is not None:
            self.on_open_search(route)

    def begin_first_route(self) -> None:
        self._switch_page(1)
        self._open_new_route()

    def show_browser_settings(self, message: str | None = None) -> None:
        self._switch_page(4)
        if message:
            self.system.set_browser_warning(message)

    def _run_now(self) -> None:
        if self.on_run_now is None:
            QMessageBox.information(self, "准备中", "监控服务正在初始化，请稍后重试。")
            return
        accepted = self.on_run_now()
        if accepted is False:
            return
        self.dashboard.set_running(True)
        self.dashboard.set_runtime_message("已请求立即查询；航程会在一个隔离浏览器中严格串行执行。")

    def _toggle_pause(self) -> None:
        callback = self.on_resume if self._paused else self.on_pause
        if callback is None:
            return
        callback()

    def _retry_leg(self, leg_id: str) -> None:
        if self.on_retry_leg is None:
            return
        accepted = self.on_retry_leg(leg_id)
        if accepted is not False:
            self.dashboard.set_running(True)
            self.system.clear_attention()

    def _open_verification(self, leg_id: str) -> None:
        if self.on_open_verification is not None:
            self.on_open_verification(leg_id)

    def open_latest_report(self) -> None:
        report = self.latest_report_path
        if report is None and self.outputs_dir and self.outputs_dir.is_dir():
            candidates = sorted(self.outputs_dir.glob("airfare-monitor_*.xlsx"), key=lambda item: item.stat().st_mtime)
            report = candidates[-1] if candidates else None
        if report is None or not report.is_file():
            QMessageBox.information(self, "暂无报告", "完成至少一轮查询后即可打开最新 Excel 报告。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(report.resolve())))

    def refresh_routes(self, routes: list[LegConfig]) -> None:
        self.dashboard.refresh(routes)
        self.routes_page.refresh(routes)
        self.history.refresh(routes)
        self.system.set_route_count(routes)

    def refresh_from_history(self) -> None:
        if self.history_store is None:
            return
        routes = self.controller.current_routes()
        rows = self.history_store.latest_leg_results([route.id for route in routes])
        self._latest_prices.clear()
        for row in rows:
            leg_id = str(row["leg_id"])
            price = _optional_decimal(row.get("minimum_total_price_cny"))
            if price is not None:
                self._latest_prices[leg_id] = price
            self.routes_page.set_leg_status(leg_id, _stored_result_status(row))
        latest_run = self.history_store.latest_run()
        self.dashboard.set_data(load_dashboard_data(self.history_store, routes))
        self.history.reload()
        self.system.refresh_events()
        if latest_run:
            finished = str(latest_run["finished_at"]).replace("T", " ")
            self.dashboard.set_runtime("最近完成", f"最近一轮：{finished} · {latest_run['status']}")
            self._set_status_message("最近完成", f"{finished} · {latest_run['status']}")

    def handle_monitor_event(self, event: object) -> None:
        if isinstance(event, CoordinatorStateChanged):
            labels = {
                "IDLE": "空闲",
                "RUNNING": "正在查询",
                "PAUSED": "已暂停",
                "ATTENTION": "需要人工处理",
                "ERROR": "运行异常",
                "EXITING": "正在退出",
            }
            title = labels.get(event.state, event.state)
            self._paused = event.state == "PAUSED"
            self.monitor_paused_changed.emit(event.state == "PAUSED")
            self.dashboard.set_paused(self._paused)
            self.dashboard.set_running(event.state == "RUNNING")
            self._set_runtime(title, event.message)
        elif isinstance(event, CycleStarted):
            self.routes_page.mark_enabled_queued()
            self.dashboard.set_running(True)
            self._set_runtime("正在查询", f"本轮共 {event.total_legs} 条航程，浏览器将严格串行执行。")
        elif isinstance(event, LegStarted):
            route = f"{event.leg.origin_airport_iata} → {event.leg.destination_airport_iata}"
            self.routes_page.set_leg_status(event.leg.id, f"查询中 {event.index}/{event.total}")
            self._set_runtime("正在查询", f"{event.index}/{event.total} · {route}")
        elif isinstance(event, LegFinished):
            status = event.result.status.value
            if status == "success" and event.minimum_total_cny is not None:
                self._latest_prices[event.result.leg.id] = event.minimum_total_cny
                route_status = f"完成 · {_price_text(event.minimum_total_cny)}"
            elif status == "success":
                self._latest_prices.pop(event.result.leg.id, None)
                route_status = "完成 · 无符合条件航班"
            elif status == "manual_attention":
                route_status = "需要人工处理"
            else:
                route_status = "查询失败"
            self.routes_page.set_leg_status(event.result.leg.id, route_status)
            self._set_status_message("正在查询", f"已完成 {event.index}/{event.total}：{route_status}")
        elif isinstance(event, CycleFinished):
            succeeded = sum(result.status.value == "success" for result in event.report.legs)
            total = event.total_legs or len(event.report.legs)
            workbook_name = Path(event.workbook_path).name
            self.latest_report_path = Path(event.workbook_path)
            self.refresh_from_history()
            self.dashboard.set_running(False)
            self._set_runtime(
                "本轮完成",
                f"成功 {succeeded}/{total} · 报告：{workbook_name}",
            )
        elif isinstance(event, NextRunScheduled):
            due = event.due_at.strftime("%m-%d %H:%M")
            self.dashboard.set_next_run(event.due_at)
            self._set_runtime("等待下轮", f"下次自动查询：{due}")
        elif isinstance(event, ManualAttentionRequested):
            self.routes_page.set_leg_status(event.leg_id, "需要人工处理")
            self.system.set_attention(event.leg_id, event.message)
            self._set_runtime("需要人工处理", event.message)
            self.system.refresh_events()
        elif isinstance(event, VerificationBrowserOpened):
            self.system.verification_opened(event.leg_id)
            self._switch_page(4)
        elif isinstance(event, MailDeliveryFailed):
            self.notifications.status.setText("价格和报告已保存，但邮件发送失败；请检查设置并主动测试通路。")
            self.refresh_from_history()
        elif isinstance(event, FatalError):
            self.dashboard.set_running(False)
            self._set_runtime("运行异常", f"{event.category}：{event.user_message}")

    def _set_runtime(self, title: str, detail: str) -> None:
        self.dashboard.set_runtime(title, detail)
        self.system.set_runtime(title, detail)
        self._set_status_message(title, detail)
        self.runtime_status_changed.emit(title)

    def activate(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.isVisible():
            event.accept()
            return
        settings = self.preferences.load()
        if not settings.close_to_tray_confirmed:
            QMessageBox.information(
                self,
                "航价守望仍会继续运行",
                "关闭窗口后应用会收至系统托盘，监控不会停止。\n"
                "如需完全退出，请使用托盘菜单中的“退出并停止监控”。",
            )
            try:
                self.preferences.repository.save_desktop(
                    replace(settings, close_to_tray_confirmed=True)
                )
            except (OSError, ValueError):
                pass
        event.accept()


class RoutesPage(QWidget):
    def __init__(
        self,
        controller: DesktopController,
        catalog: AirportCatalog,
        *,
        open_results: Callable[[LegConfig], None] | None = None,
        on_open_search: Callable[[LegConfig], None] | None = None,
    ):
        super().__init__()
        self.controller = controller
        self.catalog = catalog
        self.open_results = open_results
        self.on_open_search = on_open_search
        self.routes: list[LegConfig] = []
        self._runtime_status: dict[str, str] = {}
        self.cards: list[QFrame] = []
        self.setObjectName("pageCanvas")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(19)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(QLabel("我的航程", objectName="pageTitle"))
        heading.addWidget(QLabel("把想去的地方交给航价守望；复制的航程默认暂停。", objectName="muted"))
        header.addLayout(heading)
        header.addStretch()
        add = QPushButton("＋  添加航程", objectName="primary")
        add.clicked.connect(self._new)
        header.addWidget(add)
        layout.addLayout(header)

        capacity_card = QFrame(objectName="toolbarCard")
        capacity_row = QHBoxLayout(capacity_card)
        capacity_row.setContentsMargins(16, 10, 16, 10)
        self.capacity = QLabel(objectName="capacityText")
        capacity_row.addWidget(self.capacity)
        self.capacity_bar = QProgressBar()
        self.capacity_bar.setObjectName("capacityBar")
        self.capacity_bar.setRange(0, MAX_ENABLED_LEGS)
        self.capacity_bar.setTextVisible(False)
        self.capacity_bar.setMaximumWidth(360)
        capacity_row.addWidget(self.capacity_bar, 1)
        capacity_row.addStretch()
        layout.addWidget(capacity_card)

        self.empty_label = QLabel(
            "还没有航程。点击右上角“添加航程”，两分钟内即可开始监控。",
            objectName="emptyState",
            alignment=Qt.AlignmentFlag.AlignCenter,
            wordWrap=True,
        )
        self.scroll = QScrollArea()
        self.scroll.setObjectName("routeScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(14)
        self.cards_grid.setVerticalSpacing(14)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        self.cards_grid.setRowStretch(99, 1)
        self.scroll.setWidget(self.cards_host)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.scroll, 1)

    def refresh(self, routes: list[LegConfig]) -> None:
        self.routes = routes
        enabled = sum(route.enabled for route in routes)
        self.capacity.setText(f"已启用 {enabled} / {MAX_ENABLED_LEGS} 个航程")
        self.capacity_bar.setValue(enabled)
        for card in self.cards:
            self.cards_grid.removeWidget(card)
            card.deleteLater()
        self.cards.clear()
        self.empty_label.setVisible(not routes)
        self.scroll.setVisible(bool(routes))
        for index, route in enumerate(routes):
            card = self._route_card(route)
            self.cards.append(card)
            self.cards_grid.addWidget(card, index // 2, index % 2)

    def _route_card(self, route: LegConfig) -> QFrame:
        card = QFrame(objectName="managedRouteCard")
        # The refreshed theme uses taller controls and typography.  Reserve
        # enough vertical room for both rows in the four-field summary instead
        # of allowing QVBoxLayout to compress their labels at display scaling.
        card.setMinimumHeight(365)
        # Ignore content-driven horizontal size hints so both grid columns stay
        # visually equal even when one route has a longer transfer preference.
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 17, 20, 16)
        layout.setSpacing(13)

        top = QHBoxLayout()
        state = QLabel("运行中" if route.enabled else "已暂停")
        state.setObjectName("activePill" if route.enabled else "pausedPill")
        top.addWidget(state)
        top.addStretch()
        toggle = QPushButton("暂停" if route.enabled else "启用", objectName="routeToggle")
        toggle.clicked.connect(lambda checked=False, item=route: self._toggle(item))
        top.addWidget(toggle)
        layout.addLayout(top)

        route_row = QHBoxLayout()
        route_row.addLayout(_airport_block(route.origin_name_zh, route.origin_airport_iata))
        route_row.addStretch()
        direction = QHBoxLayout()
        direction.setSpacing(1)
        direction.addWidget(_icon_label("plane", "#176be3", "transparent", 27))
        direction.addWidget(QLabel("→" if not route.return_date else "⇄", objectName="routeArrow"))
        route_row.addLayout(direction)
        route_row.addStretch()
        route_row.addLayout(_airport_block(route.destination_name_zh, route.destination_airport_iata))
        layout.addLayout(route_row)

        tags = QHBoxLayout()
        source = "国内 · 同程" if _market_name(route) == "domestic" else "国际/跨境 · 去哪儿"
        source_label = QLabel(source, objectName="sourcePill")
        trip_label = QLabel("往返" if route.return_date else "单程", objectName="neutralPill")
        tags.addWidget(source_label)
        tags.addWidget(trip_label)
        tags.addStretch()
        layout.addLayout(tags)

        summary = QFrame(objectName="routeSummary")
        summary.setMinimumHeight(94)
        details = QGridLayout(summary)
        details.setContentsMargins(13, 11, 13, 11)
        details.setHorizontalSpacing(14)
        details.setVerticalSpacing(7)
        date_text = route.departure_date.isoformat()
        if route.return_date:
            date_text += f" — {route.return_date.isoformat()}"
        time_text = f"{route.etd_window.start.strftime('%H:%M')} — {route.etd_window.end.strftime('%H:%M')}"
        preference = "仅直达" if route.direct_only else f"允许中转 · 最长 {route.max_layover_minutes or 0} 分钟"
        threshold = _price_text(route.expected_total_price_cny) if route.expected_total_price_cny is not None else "仅观察"
        _add_detail(details, 0, 0, "出行日期", date_text)
        _add_detail(details, 1, 0, "出发时段", time_text)
        _add_detail(details, 0, 1, "行程偏好", preference)
        _add_detail(details, 1, 1, "心理价位", threshold)
        layout.addWidget(summary)

        latest = self._runtime_status.get(route.id, "等待首次查询" if route.enabled else "监控已暂停")
        latest_row = QHBoxLayout()
        latest_row.addWidget(QLabel("最近状态  ·", objectName="muted"))
        latest_value = QLabel(latest, objectName="routeLatest")
        latest_row.addWidget(latest_value)
        latest_row.addStretch()
        layout.addLayout(latest_row)

        footer = QHBoxLayout()
        edit = QPushButton("编辑", objectName="routeAction")
        edit.clicked.connect(lambda checked=False, item=route: self._edit(item))
        copy = QPushButton("复制", objectName="routeAction")
        copy.clicked.connect(lambda checked=False, item=route: self._copy(item))
        open_site = QPushButton(f"在{search_site_label(route)}打开", objectName="routeAction")
        open_site.setToolTip("用默认浏览器打开与监控同口径的来源网站搜索结果页")
        if self.on_open_search is not None:
            open_site.clicked.connect(lambda checked=False, item=route: self.on_open_search(item))
        else:
            open_site.setEnabled(False)
        delete = QPushButton("删除", objectName="dangerAction")
        delete.clicked.connect(lambda checked=False, item=route: self._delete(item))
        footer.addWidget(edit)
        footer.addWidget(copy)
        footer.addWidget(open_site)
        footer.addStretch()
        footer.addWidget(delete)
        results = QPushButton("查看候选", objectName="routeResultAction")
        results.setEnabled(self.open_results is not None)
        if self.open_results is not None:
            results.clicked.connect(lambda checked=False, item=route: self.open_results(item))
        footer.addWidget(results)
        layout.addLayout(footer)
        return card

    def set_leg_status(self, leg_id: str, status: str) -> None:
        self._runtime_status[leg_id] = status
        self.refresh(self.routes)

    def mark_enabled_queued(self) -> None:
        for route in self.routes:
            if route.enabled:
                self._runtime_status[route.id] = "排队中"
        self.refresh(self.routes)

    def _new(self) -> None:
        RouteWizard(self.catalog, self.controller, parent=self).exec()

    def _edit(self, route: LegConfig) -> None:
        RouteWizard(self.catalog, self.controller, route=route, parent=self).exec()

    def _toggle(self, route: LegConfig) -> None:
        try:
            self.controller.toggle_route(route.id, not route.enabled)
        except ValueError as exc:
            QMessageBox.warning(self, "无法启用", str(exc))

    def _copy(self, route: LegConfig) -> None:
        copied = _copy_paused(route)
        self.controller.save_route(copied)

    def _delete(self, route: LegConfig) -> None:
        answer = QMessageBox.question(self, "删除航程", "只删除监控配置，不会删除已有历史记录。确定删除吗？")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.delete_route(route.id)


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.addWidget(QLabel(title, objectName="pageTitle"))
        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel(description, wordWrap=True))
        layout.addWidget(card)
        layout.addStretch()


class SystemStatusPage(QWidget):
    settings_saved = Signal(object)

    def __init__(
        self,
        browsers: list[BrowserCandidate],
        preferences: PreferencesManager,
        *,
        history_store: SQLiteStore | None = None,
        outputs_dir: Path | None = None,
        retry_leg: Callable[[str], None] | None = None,
        open_verification: Callable[[str], None] | None = None,
    ):
        super().__init__()
        self.preferences = preferences
        self.history_store = history_store
        self.outputs_dir = outputs_dir
        self.retry_leg = retry_leg
        self.open_verification = open_verification
        self._attention_leg_id: str | None = None
        self._enabled_route_count = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget(objectName="pageHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(15)
        scroll.setWidget(host)
        outer.addWidget(scroll)
        title_row = QHBoxLayout()
        title_row.setSpacing(14)
        title_row.addWidget(_icon_label("settings", "#2878eb", "#e5f0ff", 54))
        title_copy = QVBoxLayout()
        title_copy.setSpacing(2)
        title_copy.addWidget(QLabel("系统状态", objectName="pageTitle"))
        title_copy.addWidget(
            QLabel("查看监控健康、浏览器设置与最近运行记录；运行偏好可随时调整。", objectName="muted")
        )
        title_row.addLayout(title_copy)
        title_row.addStretch()
        title_row.addWidget(QLabel(f"v{__version__}", objectName="systemVersion"))
        self.save_button = QPushButton("保存设置", objectName="systemSaveButton")
        self.save_button.setIcon(_plain_icon("save", "#ffffff"))
        self.save_button.clicked.connect(self._save_settings)
        title_row.addWidget(self.save_button)
        layout.addLayout(title_row)
        health_row = QHBoxLayout()
        health_row.setSpacing(14)
        self.service_health = _health_card("监控服务", "等待启动", "监控服务当前状态", "status", "green")
        self.storage_health = _health_card(
            "数据存储",
            "正常" if history_store is not None else "不可用",
            "数据存储状态良好" if history_store is not None else "无法读取价格数据库",
            "database",
            "blue",
        )
        self.query_health = _health_card("航班查询", "等待首次查询", "系统运行状态", "plane", "violet")
        for card in (self.service_health, self.storage_health, self.query_health):
            health_row.addWidget(card, 1)
        layout.addLayout(health_row)
        self.routes_label = QLabel("启用航程：0 / 10")
        settings = self.preferences.load()
        self.form = RuntimePreferencesForm(browsers, settings)
        self.form.redetect_requested.connect(self._redetect)
        layout.addWidget(
            preference_card(
                "浏览器与自动查询",
                "设置会保存到本机用户目录；浏览器显示方式从下一轮查询开始生效。",
                self.form,
            )
        )
        self.browser_warning = QLabel(objectName="warningText", wordWrap=True)
        self.browser_warning.hide()
        layout.addWidget(self.browser_warning)

        profile = _system_info_card(
            "cube",
            "独立浏览器空间",
            "航价守望使用自己的浏览器 Profile，不会读取或修改你日常 Chrome/Edge 的收藏、Cookie 和登录状态。",
        )
        layout.addWidget(profile)

        self.attention_card = QFrame(objectName="warningCard")
        attention_layout = QVBoxLayout(self.attention_card)
        attention_layout.setContentsMargins(23, 20, 23, 20)
        attention_layout.setSpacing(15)
        attention_layout.addWidget(QLabel("需要你完成一次页面确认", objectName="sectionTitle"))
        self.attention_text = QLabel(wordWrap=True)
        attention_layout.addWidget(self.attention_text)
        steps = QHBoxLayout()
        for number, heading, detail in (
            ("1", "打开验证页面", "在隔离浏览器中打开受影响航程的页面"),
            ("2", "人工完成确认", "按照网站提示，在浏览器中自行完成操作"),
            ("3", "返回并重新查询", "确认完成后，手动重试这条航程"),
        ):
            step = QFrame(objectName="card")
            step_layout = QVBoxLayout(step)
            step_layout.addWidget(QLabel(f"{number}  {heading}", objectName="sectionTitle"))
            step_layout.addWidget(QLabel(detail, objectName="muted", wordWrap=True))
            steps.addWidget(step, 1)
        attention_layout.addLayout(steps)
        attention_layout.addWidget(QLabel("航价守望不会代替你处理验证，也不会自动登录或保存网站账号。", objectName="muted", wordWrap=True))
        attention_actions = QHBoxLayout()
        attention_actions.addStretch()
        open_page = QPushButton("打开验证页面")
        open_page.clicked.connect(self._open_attention)
        retry = QPushButton("我已完成，重新查询", objectName="primary")
        retry.clicked.connect(self._retry_attention)
        later = QPushButton("稍后处理")
        later.clicked.connect(self.attention_card.hide)
        attention_actions.addWidget(later)
        attention_actions.addWidget(open_page)
        attention_actions.addWidget(retry)
        attention_layout.addLayout(attention_actions)
        self.attention_card.hide()
        layout.addWidget(self.attention_card)

        paths_card = QFrame(objectName="systemInfoCard")
        paths_layout = QHBoxLayout(paths_card)
        paths_layout.setContentsMargins(20, 13, 20, 13)
        paths_layout.setSpacing(13)
        paths_layout.addWidget(_icon_label("folder", "#2878eb", "#e8f2ff", 42), alignment=Qt.AlignmentFlag.AlignTop)
        paths_copy = QVBoxLayout()
        paths_copy.setSpacing(3)
        paths_copy.addWidget(QLabel("本地数据位置", objectName="systemSectionTitle"))
        profile_path = self.preferences.repository.load_core().browser.user_data_path
        paths_copy.addWidget(QLabel(f"独立 Profile：{profile_path}", objectName="muted", wordWrap=True))
        if history_store is not None:
            paths_copy.addWidget(QLabel(f"价格数据库：{history_store.path}", objectName="muted", wordWrap=True))
        if outputs_dir is not None:
            open_outputs = QPushButton("打开报告目录", objectName="systemOutlineButton")
            open_outputs.setIcon(_plain_icon("folder", "#176be3"))
            open_outputs.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(outputs_dir.resolve())))
            )
            paths_copy.addWidget(open_outputs, alignment=Qt.AlignmentFlag.AlignLeft)
        paths_layout.addLayout(paths_copy, 1)
        layout.addWidget(paths_card)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(24)
        self.routes_label.setObjectName("systemSummary")
        summary_row.addWidget(self.routes_label)
        self.runtime_label = QLabel("运行状态：等待启动", objectName="systemSummary")
        summary_row.addWidget(self.runtime_label)
        summary_row.addStretch()
        layout.addLayout(summary_row)
        self.runtime_detail_label = QLabel("监控服务尚未启动。", objectName="muted", wordWrap=True)
        layout.addWidget(self.runtime_detail_label)
        events_card = QFrame(objectName="card")
        events_layout = QVBoxLayout(events_card)
        events_layout.setContentsMargins(20, 16, 20, 16)
        events_layout.addWidget(QLabel("最近运行记录", objectName="sectionTitle"))
        self.events_table = QTableWidget(0, 3)
        self.events_table.setHorizontalHeaderLabels(["时间", "级别", "动态"])
        self.events_table.horizontalHeader().setStretchLastSection(True)
        self.events_table.verticalHeader().setVisible(False)
        self.events_table.setMinimumHeight(150)
        self.events_table.setMaximumHeight(205)
        events_layout.addWidget(self.events_table)
        export = QPushButton("导出脱敏诊断包")
        export.clicked.connect(self._export_diagnostics)
        events_layout.addWidget(export, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(events_card)
        layout.addStretch()
        self.refresh_events()

    def _export_diagnostics(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self, "保存脱敏诊断包", "AirfareMonitor-diagnostics.zip", "ZIP 文件 (*.zip)"
        )
        if not filename:
            return
        try:
            settings = self.preferences.load()
            browser = self.preferences.selected_browser(settings)
            logs_dir = self.history_store.path.parent.parent / "logs" if self.history_store is not None else Path(".")
            export_diagnostic_zip(
                Path(filename), settings, self.history_store,
                enabled_routes=self._enabled_route_count,
                logs_dir=logs_dir, catalog_version=self.catalog.version,
                browser_version=browser.version if browser is not None else None,
            )
        except (OSError, ValueError):
            QMessageBox.warning(self, "无法导出", "诊断信息未保存，请选择可写入的位置。")
            return
        QMessageBox.information(self, "已导出", "脱敏诊断包已保存；不含授权码、邮箱地址、原始网页响应或浏览器数据。")

    def refresh_events(self) -> None:
        if self.history_store is None:
            self.events_table.setRowCount(0)
            return
        events = self.history_store.recent_app_events(limit=12)
        self.events_table.setRowCount(len(events))
        for index, event in enumerate(events):
            stamp = str(event.get("occurred_at") or "").replace("T", " ")[:16]
            severity = {"info": "信息", "notice": "提醒", "warning": "注意", "error": "异常"}.get(
                str(event.get("severity")), "信息"
            )
            for column, text in enumerate((stamp, severity, str(event.get("message") or ""))):
                self.events_table.setItem(index, column, QTableWidgetItem(text))
        self.events_table.resizeColumnsToContents()

    def _redetect(self) -> None:
        browsers = self.preferences.refresh_browsers()
        current = self.preferences.load()
        self.form.set_browsers(
            browsers,
            preferred_path=current.browser_path,
            preferred_kind=current.browser_kind,
        )
        if browsers:
            self.browser_warning.hide()

    def _save_settings(self) -> None:
        try:
            saved = self.preferences.save(self.form.values(onboarding_completed=True))
        except Exception as exc:
            QMessageBox.warning(self, "设置未保存", str(exc))
            return
        self.form.load(saved)
        self.browser_warning.hide()
        self.settings_saved.emit(saved)
        QMessageBox.information(self, "设置已保存", "新的运行设置将从下一轮查询开始生效。")

    def set_browser_warning(self, message: str) -> None:
        self.browser_warning.setText(message)
        self.browser_warning.show()

    def set_route_count(self, routes: list[LegConfig]) -> None:
        self._enabled_route_count = sum(route.enabled for route in routes)
        self.routes_label.setText(f"启用航程：{self._enabled_route_count} / {MAX_ENABLED_LEGS}")

    def set_runtime(self, title: str, detail: str) -> None:
        self.runtime_label.setText(f"运行状态：{title}")
        self.runtime_detail_label.setText(detail)
        _set_health(self.service_health, title)
        if title in {"本轮完成", "等待下轮", "最近完成"}:
            _set_health(self.query_health, "最近查询正常")
        elif title in {"运行异常", "需要人工处理"}:
            _set_health(self.query_health, title)

    def set_attention(self, leg_id: str, message: str) -> None:
        self._attention_leg_id = leg_id
        self.attention_text.setText(
            f"航程 {leg_id} 需要人工处理。{message}\n"
            "1. 打开航价守望自己的可见浏览器；2. 人工完成网页提示；3. 点击已完成重新查询。"
        )
        self.attention_card.show()

    def verification_opened(self, leg_id: str) -> None:
        self._attention_leg_id = leg_id
        self.attention_text.setText(
            "已打开使用同一隔离 Profile 的可见浏览器。请只在网页中人工完成提示，"
            "然后点击“我已完成，重新查询”。下次监控仍使用原来的显示/隐藏设置。"
        )
        self.attention_card.show()

    def _open_attention(self) -> None:
        if self._attention_leg_id and self.open_verification:
            self.open_verification(self._attention_leg_id)

    def clear_attention(self) -> None:
        self._attention_leg_id = None
        self.attention_card.hide()

    def _retry_attention(self) -> None:
        if self._attention_leg_id and self.retry_leg:
            self.retry_leg(self._attention_leg_id)


def _airport_block(name: str | None, iata: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(0)
    layout.addWidget(QLabel(name or iata, objectName="routeCity"))
    layout.addWidget(QLabel(iata, objectName="routeCode"))
    return layout


def _add_detail(layout: QGridLayout, row: int, column: int, title: str, value: str) -> None:
    block = QVBoxLayout()
    block.setSpacing(2)
    block.addWidget(QLabel(title, objectName="detailLabel"))
    block.addWidget(QLabel(value, objectName="detailValue"))
    layout.addLayout(block, row, column)


def _health_card(title: str, value: str, detail: str, icon: str, accent: str) -> QFrame:
    tones = {
        "green": ("#19a56e", "#ddf7eb"),
        "blue": ("#2878eb", "#e6f0ff"),
        "violet": ("#7258de", "#eeeaff"),
    }
    color, background = tones[accent]
    card = QFrame(objectName="systemHealthCard")
    card.setProperty("accent", accent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(3)
    row = QHBoxLayout()
    row.setSpacing(9)
    row.addWidget(_icon_label(icon, color, background, 32))
    row.addWidget(QLabel(title, objectName="systemHealthTitle"))
    row.addStretch()
    layout.addLayout(row)
    layout.addWidget(QLabel(value, objectName="healthValue"))
    layout.addWidget(QLabel(detail, objectName="systemHealthDetail"))
    return card


def _set_health(card: QFrame, value: str) -> None:
    card.findChild(QLabel, "healthValue").setText(value)


def _system_info_card(icon: str, title: str, detail: str) -> QFrame:
    card = QFrame(objectName="systemInfoCard")
    layout = QHBoxLayout(card)
    layout.setContentsMargins(20, 13, 20, 13)
    layout.setSpacing(13)
    layout.addWidget(_icon_label(icon, "#2878eb", "#e8f2ff", 42))
    copy = QVBoxLayout()
    copy.setSpacing(3)
    copy.addWidget(QLabel(title, objectName="systemSectionTitle"))
    copy.addWidget(QLabel(detail, objectName="muted", wordWrap=True))
    layout.addLayout(copy, 1)
    layout.addWidget(_icon_label(icon, "#b8d5fb", "transparent", 48))
    return card


def _market_name(route: LegConfig) -> str:
    from ..market import resolve_market
    try:
        return resolve_market(route)
    except ValueError:
        return route.market


def _copy_paused(route: LegConfig) -> LegConfig:
    from dataclasses import replace
    import uuid
    return replace(route, id=f"route-{uuid.uuid4().hex[:8]}", enabled=False)


def _price_text(value: Decimal) -> str:
    return f"¥{value:,.0f}"


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _stored_result_status(row: dict[str, object]) -> str:
    status = str(row.get("status", ""))
    price = _optional_decimal(row.get("minimum_total_price_cny"))
    if status == "success" and price is not None:
        return f"完成 · {_price_text(price)}"
    if status == "success":
        return "完成 · 无符合条件航班"
    if status == "manual_attention":
        return "需要人工处理"
    return "最近查询失败"
