"""托盘交互接线：左键开主窗口、右键菜单（主窗口隐藏时先拉起主窗口保激活），
以及上次未正常结束提示改为非阻塞横幅。"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from airfare_monitor.desktop_app import application as app_module


class _StubWindow(QObject):
    monitor_paused_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.activated = 0
        self.visible = False

    def activate(self) -> None:
        self.activated += 1

    def isVisible(self) -> bool:  # noqa: N802 - MainWindow API 口径
        return self.visible

    def _run_now(self) -> None:
        return None

    def open_latest_report(self) -> None:
        return None


class _StubCoordinator:
    def pause(self) -> None:
        return None

    def resume(self) -> None:
        return None


class _FakeTray:
    def __init__(self, supports_messages: bool = True) -> None:
        self.messages: list[tuple[tuple, dict]] = []
        self._supports = supports_messages

    def supportsMessages(self) -> bool:  # noqa: N802 - QSystemTrayIcon API 口径
        return self._supports

    def showMessage(self, *args, **kwargs) -> None:
        self.messages.append((args, kwargs))


class _FakeApp(QObject):
    applicationStateChanged = Signal(object)


class TrayInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _create_tray(self, window: _StubWindow) -> tuple[QSystemTrayIcon, list]:
        # 强制走 macOS ≤6.11.2 的 QTBUG-147449 防崩分支，保证未来升级
        # PySide6 ≥6.11.3（回到原生菜单分支）后本用例仍验证这条旧路径。
        with (
            mock.patch.object(app_module, "_native_tray_menu_supported", return_value=False),
            mock.patch.object(app_module.sys, "platform", "darwin"),
        ):
            tray = app_module._create_tray(self.app, window, _StubCoordinator())
        popups: list = []
        tray._menu.popup = lambda *args, **kwargs: popups.append(args)
        return tray, popups

    def test_left_click_opens_main_window_without_menu(self):
        window = _StubWindow()
        tray, popups = self._create_tray(window)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        QTest.qWait(50)
        self.assertEqual(window.activated, 1)
        self.assertEqual(popups, [])

    def test_double_and_middle_click_also_open_main_window(self):
        window = _StubWindow()
        tray, popups = self._create_tray(window)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.MiddleClick)
        QTest.qWait(50)
        self.assertEqual(window.activated, 2)
        self.assertEqual(popups, [])

    def test_right_click_with_hidden_window_raises_window_then_menu(self):
        window = _StubWindow()
        window.visible = False
        tray, popups = self._create_tray(window)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        QTest.qWait(50)
        self.assertEqual(window.activated, 1)
        self.assertEqual(len(popups), 1)

    def test_right_click_with_visible_window_pops_menu_only(self):
        window = _StubWindow()
        window.visible = True
        tray, popups = self._create_tray(window)
        tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        QTest.qWait(50)
        self.assertEqual(window.activated, 0)
        self.assertEqual(len(popups), 1)

    def test_unclean_session_notification_is_non_blocking_banner(self):
        tray = _FakeTray(supports_messages=True)
        window = _StubWindow()
        target = [0]
        app_module._notify_previous_unclean_session(tray, window, target)
        self.assertEqual(target[0], 4)  # 点击横幅直达系统状态页
        self.assertEqual(len(tray.messages), 1)
        args = tray.messages[0][0]
        self.assertEqual(args[0], "上次运行未正常结束")
        self.assertIs(args[2], QSystemTrayIcon.MessageIcon.Warning)
        self.assertFalse(hasattr(window, "_unclean_exit_notice"))

    def test_unclean_session_falls_back_to_non_modal_dialog(self):
        tray = _FakeTray(supports_messages=False)
        window = _StubWindow()
        target = [0]
        created: list = []

        class _FakeBox:
            Icon = type("Icon", (), {"Warning": "warning"})
            StandardButton = type("StandardButton", (), {"Ok": "ok"})

            def __init__(self, *args) -> None:
                self.args = args
                self.modal = True
                self.shown = False
                created.append(self)

            def setModal(self, value: bool) -> None:
                self.modal = value

            def show(self) -> None:
                self.shown = True

        with mock.patch.object(app_module, "QMessageBox", _FakeBox):
            app_module._notify_previous_unclean_session(tray, window, target)
        self.assertEqual(tray.messages, [])
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].modal, "兜底对话框必须非模态，不得吃掉全应用输入")
        self.assertTrue(created[0].shown)
        self.assertIs(window._unclean_exit_notice, created[0])
        self.assertEqual(target[0], 4)

    def test_reopen_restores_hidden_window(self):
        window = _StubWindow()
        window.visible = False
        fake_app = _FakeApp()
        app_module._install_reopen_handler(fake_app, window, arm_after_ms=0)  # type: ignore[arg-type]
        QTest.qWait(20)  # 让 arm_after_ms=0 的布防定时器触发
        fake_app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
        self.assertEqual(window.activated, 0)
        fake_app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(window.activated, 1)

    def test_reopen_quarantined_during_launch_activation(self):
        window = _StubWindow()
        window.visible = False
        fake_app = _FakeApp()
        # arm_after_ms 足够大：模拟启动后 3 秒内的「启动激活」，
        # 托盘模式启动瞬间不应被顶出主窗口。
        app_module._install_reopen_handler(fake_app, window, arm_after_ms=60000)  # type: ignore[arg-type]
        fake_app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        QTest.qWait(20)
        self.assertEqual(window.activated, 0, "布防前的 Active 转换是启动激活，不得唤出主窗口")

    def test_reopen_leaves_visible_window_alone(self):
        window = _StubWindow()
        window.visible = True
        fake_app = _FakeApp()
        app_module._install_reopen_handler(fake_app, window, arm_after_ms=0)  # type: ignore[arg-type]
        QTest.qWait(20)
        fake_app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(window.activated, 0)


if __name__ == "__main__":
    unittest.main()
