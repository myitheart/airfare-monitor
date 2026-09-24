"""Desktop composition root: paths, controllers, UI and tray."""

from __future__ import annotations

import sys
import logging
from pathlib import Path

from PySide6.QtCore import QUrl, QTimer, Qt
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon
import PySide6

from ..app_paths import AppPaths
from ..config import load_settings
from ..models import LegConfig
from ..search_link import search_url_for
from ..storage import SQLiteStore
from ..ui.app_icon import application_icon
from ..ui.i18n import install_chinese_translations
from ..ui.main_window import MainWindow
from ..ui.onboarding import OnboardingDialog
from .airport_catalog import AirportCatalog
from .credential_store import CredentialStore
from .controller import DesktopController
from .event_bridge import CoordinatorEventBridge
from .event_journal import AppEventJournal
from .events import CoordinatorStateChanged, CycleFinished
from .monitor_coordinator import MonitorCoordinator
from .notification_policy import alert_for_event
from .preferences import PreferencesManager
from .route_repository import RouteRepository
from .settings_repository import SettingsRepository
from .single_instance import SingleInstance
from .session_state import DesktopSessionState
from .startup import initialize_desktop


logger = logging.getLogger(__name__)


def validate_ui_runtime(paths: AppPaths) -> str:
    """Construct the main window without starting monitoring or the event loop."""
    initialize_desktop(paths)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("航价守望")
    app.setWindowIcon(application_icon())
    _apply_style(app, paths.resource_root)
    install_chinese_translations(app, paths.resource_root)
    catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
    controller = DesktopController(RouteRepository(paths.routes_path))
    preferences = PreferencesManager(SettingsRepository(paths.settings_path, user_root=paths.user_root))
    browsers = preferences.refresh_browsers()
    onboarding = OnboardingDialog(preferences)
    onboarding.close()
    window = MainWindow(
        controller,
        catalog,
        browsers,
        preferences,
        on_run_now=lambda: None,
        history_store=SQLiteStore(paths.database_path),
        outputs_dir=paths.outputs_dir,
    )
    window.close()
    # Read-only check catches missing dynamic keyring backends in frozen builds.
    CredentialStore._backend()
    return QGuiApplication.platformName()


def run_desktop(paths: AppPaths, *, start_hidden: bool = False) -> int:
    initialize_desktop(paths)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("航价守望")
    app.setOrganizationName("AirfareMonitor")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    _apply_style(app, paths.resource_root)
    install_chinese_translations(app, paths.resource_root)
    catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
    controller = DesktopController(RouteRepository(paths.routes_path))
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    session_state = DesktopSessionState(paths.logs_dir)
    previous_unclean = session_state.begin()
    preferences = PreferencesManager(SettingsRepository(paths.settings_path, user_root=paths.user_root))
    desktop_settings = preferences.load()
    browsers = preferences.refresh_browsers()
    add_first_route = False
    if not desktop_settings.onboarding_completed:
        onboarding = OnboardingDialog(preferences)
        if onboarding.exec() != QDialog.DialogCode.Accepted:
            session_state.finish()
            instance.close()
            return 0
        assert onboarding.saved_settings is not None
        desktop_settings = onboarding.saved_settings
        browsers = preferences.browsers
        add_first_route = not controller.current_routes()

    coordinator = MonitorCoordinator(paths)
    history_store = SQLiteStore(paths.database_path)
    journal = AppEventJournal(history_store)
    journal.initialize()
    if previous_unclean:
        journal.store.record_app_event(
            event_type="previous_unclean_exit",
            severity="warning",
            message="上次运行未正常结束；请检查浏览器与最近查询状态",
        )
        logger.warning("检测到上次桌面会话未正常结束")

    window: MainWindow

    def run_if_ready() -> bool:
        current = preferences.load()
        if preferences.selected_browser(current) is None:
            message = "没有找到已选择的 Chrome 或 Edge，请在系统状态中重新检测并保存。"
            window.show_browser_settings(message)
            QMessageBox.warning(window, "需要浏览器", message)
            return False
        return coordinator.run_now()

    def retry_if_ready(leg_id: str) -> bool:
        current = preferences.load()
        if preferences.selected_browser(current) is None:
            message = "没有找到已选择的 Chrome 或 Edge，请先重新检测并保存。"
            window.show_browser_settings(message)
            QMessageBox.warning(window, "需要浏览器", message)
            return False
        return coordinator.retry_leg(leg_id)

    def open_verification_if_ready(leg_id: str) -> bool:
        accepted = coordinator.open_verification(leg_id)
        if not accepted:
            QMessageBox.information(
                window, "暂时无法打开", "请等待当前查询结束，或先完成已打开的人工确认页面。"
            )
        return accepted

    def open_search_link(route: LegConfig) -> None:
        try:
            settings = load_settings(paths.settings_path, project_root=paths.user_root)
            QDesktopServices.openUrl(QUrl(search_url_for(route, settings)))
        except Exception:
            logger.exception("构造来源网站链接失败")
            QMessageBox.warning(window, "无法打开来源网站", "构造搜索链接失败，请检查航程的机场与日期设置。")

    window = MainWindow(
        controller,
        catalog,
        browsers,
        preferences,
        on_run_now=run_if_ready,
        on_pause=coordinator.pause,
        on_resume=coordinator.resume,
        on_retry_leg=retry_if_ready,
        on_open_verification=open_verification_if_ready,
        on_open_search=open_search_link,
        history_store=history_store,
        outputs_dir=paths.outputs_dir,
    )

    paused_for_no_routes = False

    def routes_changed(routes: list[object]) -> None:
        nonlocal paused_for_no_routes
        enabled = sum(bool(getattr(route, "enabled", False)) for route in routes)
        journal.record_routes_changed(enabled)
        if enabled:
            if paused_for_no_routes:
                paused_for_no_routes = False
                coordinator.resume()
            else:
                run_if_ready()
        else:
            was_paused = coordinator.snapshot().paused
            coordinator.pause()
            paused_for_no_routes = not was_paused

    controller.on_routes_changed(routes_changed)

    tray_mode = desktop_settings.launch_to_tray

    def apply_runtime_settings(saved: object) -> None:
        nonlocal tray_mode
        journal.record_settings_changed()
        coordinator.apply_settings()
        window.refresh_from_history()
        # 托盘模式即时生效：切换 Dock 图标可见性，无需重启应用。
        # 系统状态页保存传 DesktopSettings；通知设置页保存转发 None——
        # 此时以持久化设置为准，避免把运行中的托盘模式误关。
        tray_mode = _resolve_tray_mode(saved, preferences)
        _apply_dock_visibility(not tray_mode)

    def enforce_tray_mode_on_active(state: Qt.ApplicationState) -> None:
        # LaunchServices 会在 open/再次拉起时把 UIElement 应用提升回
        # Foreground（Dock 图标复现）；每次激活时重新断言 Accessory。
        if tray_mode and state == Qt.ApplicationState.ApplicationActive:
            _apply_dock_visibility(False)

    app.applicationStateChanged.connect(enforce_tray_mode_on_active)
    window.runtime_settings_saved.connect(apply_runtime_settings)
    bridge = CoordinatorEventBridge(app)
    bridge.event_received.connect(window.handle_monitor_event, Qt.ConnectionType.QueuedConnection)
    coordinator.subscribe(journal.record)
    coordinator.subscribe(bridge.publish)
    # Date expiry is a local configuration check and must also run when no
    # usable browser is configured, so perform it before starting the worker.
    coordinator.reconcile_expired_routes()
    instance.set_activation_handler(window.activate)
    _install_reopen_handler(app, window)
    tray = _create_tray(app, window, coordinator)
    alert_target = [0]

    def notify_desktop(event: object) -> None:
        low_price_details = (
            journal.low_price_alert_details(event.report.run_id)
            if isinstance(event, CycleFinished) else None
        )
        alert = alert_for_event(event, low_price_details=low_price_details)
        if alert is None:
            return
        alert_target[0] = alert.target_page
        if preferences.load().desktop_notifications and tray.isVisible():
            tray.showMessage(alert.title, alert.message, QSystemTrayIcon.MessageIcon.Information, 8000)

    bridge.event_received.connect(notify_desktop, Qt.ConnectionType.QueuedConnection)
    tray.messageClicked.connect(lambda: (window.activate(), window._switch_page(alert_target[0])))
    # 状态文案会被协调器事件持续刷新，操作指引必须随行，否则用户看不到。
    window.runtime_status_changed.connect(
        lambda text: tray.setToolTip(f"航价守望 · {text}（左键打开 · 右键菜单）")
    )
    def finish_desktop() -> None:
        try:
            monitor_stopped = coordinator.shutdown(timeout=20)
            mail_stopped = window.notifications.finish_pending_test()
            if monitor_stopped and mail_stopped:
                session_state.finish()
            else:
                logger.error("桌面退出等待超时，保留异常会话标记以便下次启动提示")
        finally:
            instance.close()

    app.aboutToQuit.connect(finish_desktop)
    selected_browser = preferences.selected_browser(desktop_settings)
    # 托盘模式（系统状态设置）与 --background（登录启动项）同口径：
    # 启动不弹主窗口、隐藏 Dock 图标，纯菜单栏驻留；
    # 未检测到浏览器时仍弹出主窗口引导完成设置。
    if tray_mode:
        _apply_dock_visibility(False)
    if not (start_hidden or desktop_settings.launch_to_tray) or selected_browser is None:
        window.show()
    tray.show()
    if previous_unclean:
        _notify_previous_unclean_session(tray, window, alert_target)
    coordinator.start(run_immediately=selected_browser is not None)
    if selected_browser is None:
        window.handle_monitor_event(
            CoordinatorStateChanged("ERROR", "未检测到可用浏览器；完成浏览器设置前不会开始查询")
        )
        window.show_browser_settings("安装或重新选择 Chrome/Edge 后即可开始查询。")
    if add_first_route:
        QTimer.singleShot(0, window.begin_first_route)
    return app.exec()


def _resolve_tray_mode(saved: object, preferences: PreferencesManager) -> bool:
    """托盘模式当前值：saved 为 DesktopSettings 时直接取；通知设置页保存
    经 runtime_settings_saved.emit(None) 转发，此时回读持久化设置。"""
    if saved is None:
        return preferences.load().launch_to_tray
    return bool(getattr(saved, "launch_to_tray", False))


def _apply_dock_visibility(visible: bool) -> None:
    """托盘模式 = 纯菜单栏应用：隐藏 Dock 图标与 Cmd-Tab 条目。

    对应 NSApplication 激活策略切换 Regular(0) / Accessory(1)；
    PySide6 6.11 未暴露 setDockIconVisible，走 objc_msgSend。
    Accessory 应用仍可显示窗口并在窗口聚焦时持有菜单栏。
    """
    if sys.platform != "darwin":
        return
    import ctypes

    lib = ctypes.cdll.LoadLibrary(None)
    lib.objc_getClass.restype = ctypes.c_void_p
    lib.objc_getClass.argtypes = [ctypes.c_char_p]
    lib.sel_registerName.restype = ctypes.c_void_p
    lib.sel_registerName.argtypes = [ctypes.c_char_p]
    msg = lib.objc_msgSend
    msg.restype = ctypes.c_void_p
    msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    ns_app = msg(lib.objc_getClass(b"NSApplication"), lib.sel_registerName(b"sharedApplication"))
    set_policy = lib.objc_msgSend
    set_policy.restype = None
    set_policy.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    # NSApplicationActivationPolicyRegular=0, Accessory=1
    set_policy(ns_app, lib.sel_registerName(b"setActivationPolicy:"), 0 if visible else 1)


def _install_reopen_handler(app: QApplication, window: MainWindow, *, arm_after_ms: int = 3000) -> None:
    """Dock 图标点击 / open -a 已运行实例：macOS 只激活应用、不重启进程，
    SingleInstance 的 socket 路径不会触发；系统 reopen 事件在 Qt 里表现为
    应用进入 Active 态。此时若主窗口不可见（收在托盘），按 macOS 惯例把
    窗口带回来，否则用户没有任何入口恢复界面。

    启动后 arm_after_ms 内的 Active 转换视为「启动激活」（open/双击/Finder
    拉起时系统自带的激活），不当作用户 reopen——否则托盘模式（启动不弹
    主窗口）会在启动瞬间被这次激活顶掉。
    """
    armed = [False]

    def _arm() -> None:
        armed[0] = True

    QTimer.singleShot(arm_after_ms, _arm)

    def restore(state: Qt.ApplicationState) -> None:
        if armed[0] and state == Qt.ApplicationState.ApplicationActive and not window.isVisible():
            window.activate()

    app.applicationStateChanged.connect(restore)


def _notify_previous_unclean_session(
    tray: QSystemTrayIcon, window: MainWindow, alert_target: list[int]
) -> None:
    """上次未正常结束只发非阻塞提醒。

    应用级模态 QMessageBox 会吃掉整个应用的输入事件（托盘点击、菜单、
    输入全部无响应，直到用户注意到角落里的框），是“应用卡住”误报的
    来源之一；事件详情本来就会写入系统状态页，提醒点击也直达该页。
    通知横幅不可用（未授权/无托盘消息支持）时退化为非模态对话框，
    绝不回到阻塞式提示。
    """
    alert_target[0] = 4  # 系统状态页，与 notification_policy 的目标页口径一致
    if tray.supportsMessages():
        tray.showMessage(
            "上次运行未正常结束",
            "请打开系统状态检查浏览器和最近查询记录。",
            QSystemTrayIcon.MessageIcon.Warning,
            8000,
        )
        return
    notice = QMessageBox(
        QMessageBox.Icon.Warning,
        "上次运行未正常结束",
        "航价守望检测到上次运行未正常退出。请打开系统状态检查浏览器和最近查询记录。",
        QMessageBox.StandardButton.Ok,
        window,
    )
    notice.setModal(False)
    window._unclean_exit_notice = notice  # 保持引用，避免对话框被回收
    notice.show()


def _apply_style(app: QApplication, resource_root: Path) -> None:
    stylesheet = resource_root / "styles.qss"
    if stylesheet.is_file():
        app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))


def _native_tray_menu_supported() -> bool:
    """QTBUG-147449（macOS 27 点击 QSystemTrayIcon 崩溃）的修复版本判断。

    修复 commit 6192d9ed 于 2026-08 合入 6.11 分支，但 v6.11.2 tag 冻结早于
    该合入，因此修复随 6.11.3 / 6.12 起（含）发布。6.11.2 及更早版本仍会崩，
    必须使用 activated()+QMenu.popup 的防崩路径。
    """
    try:
        major, minor, patch = (int(part) for part in PySide6.__version__.split(".")[:3])
    except ValueError:
        return False
    if (major, minor) > (6, 11):
        return True
    return (major, minor) == (6, 11) and patch >= 3


def _create_tray(app: QApplication, window: MainWindow, coordinator: MonitorCoordinator) -> QSystemTrayIcon:
    icon = application_icon()
    tray = QSystemTrayIcon(icon, app)
    menu = QMenu(objectName="trayMenu")
    # QSS 圆角需要半透明窗口背景配合,否则四角会残留面板底色
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # Keep the menu alive without setContextMenu(); otherwise it can be GC'd.
    tray._menu = menu  # type: ignore[attr-defined]
    open_action = menu.addAction("打开航价守望")
    open_action.triggered.connect(window.activate)
    run_action = menu.addAction("立即查询")
    run_action.triggered.connect(window._run_now)
    pause_action = menu.addAction("暂停监控")
    pause_action.setCheckable(True)

    def toggle_pause(paused: bool) -> None:
        if paused:
            coordinator.pause()
        else:
            coordinator.resume()
        pause_action.setText("继续监控" if paused else "暂停监控")

    def sync_pause(paused: bool) -> None:
        pause_action.blockSignals(True)
        pause_action.setChecked(paused)
        pause_action.setText("继续监控" if paused else "暂停监控")
        pause_action.blockSignals(False)

    pause_action.toggled.connect(toggle_pause)
    window.monitor_paused_changed.connect(sync_pause)
    report_action = menu.addAction("打开最新报告")
    report_action.triggered.connect(window.open_latest_report)
    menu.addSeparator()
    quit_action = menu.addAction("退出并停止监控")
    quit_action.triggered.connect(app.quit)

    if sys.platform == "darwin" and not _native_tray_menu_supported():
        # macOS 27 + Qt Cocoa（≤6.11.2，QTBUG-147449 未修复版）:
        # setContextMenu() 会让 Qt 在状态栏菜单路径上对 KitDefined 事件调用
        # -[NSEvent clickCount] 触发断言崩溃，只能由 activated() 自行分发。
        # macOS 点状态栏图标不会激活应用，而后台应用的 QMenu 弹窗拿不到
        # key window，实测会在一秒内自灭（用户表现为“点了没反应”）；
        # showNormal() 呈现主窗口后应用即被系统激活，菜单才能存活。因此：
        # 左键=直接打开主窗口（从托盘“启动”的预期），右键=菜单；主窗口
        # 隐藏时弹菜单前先拉起主窗口。升级 PySide6 ≥6.11.3 自动回原生菜单。
        tray.setToolTip("航价守望 · 左键打开主窗口，右键打开菜单")

        def show_menu() -> None:
            # 再延一拍：不要在 Cocoa 状态栏点击的通知观察者栈内同步弹菜单，
            # 退出该栈后由事件循环统一处理，进一步避开 AppKit 断言路径。
            if not window.isVisible():
                window.activate()
            QTimer.singleShot(0, lambda: menu.popup(QCursor.pos()))

        def on_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
            if reason == QSystemTrayIcon.ActivationReason.Context:
                show_menu()
            elif reason in {
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
                QSystemTrayIcon.ActivationReason.MiddleClick,
            }:
                window.activate()

        tray.activated.connect(on_activated)
    else:
        tray.setToolTip("航价守望 · 等待监控（右键打开菜单）")
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: window.activate()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
    return tray
