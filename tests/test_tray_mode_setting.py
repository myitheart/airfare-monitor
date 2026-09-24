"""托盘模式设置：DesktopSettings.launch_to_tray 的持久化、表单接线与取值。"""

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.settings_repository import DesktopSettings, SettingsRepository
from airfare_monitor.ui.preferences import RuntimePreferencesForm

ROOT = Path(__file__).resolve().parents[1]


def _paths(root: str) -> AppPaths:
    found = AppPaths.discover(user_root=root)
    values = {name: getattr(found, name) for name in found.__dataclass_fields__}
    values["resource_root"] = ROOT / "resources"
    return AppPaths(**values)


class TrayModeSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_launch_to_tray_round_trips_through_yaml(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repo = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            self.assertFalse(repo.load_desktop().launch_to_tray, "缺省必须关闭，老用户行为不变")
            repo.save_desktop(replace(repo.load_desktop(), launch_to_tray=True))
            self.assertTrue(SettingsRepository(paths.settings_path, user_root=paths.user_root).load_desktop().launch_to_tray)
            repo.save_desktop(replace(repo.load_desktop(), launch_to_tray=False))
            self.assertFalse(repo.load_desktop().launch_to_tray)

    def test_launch_to_tray_rejects_non_boolean(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings_file = paths.settings_path
            text = settings_file.read_text(encoding="utf-8")
            settings_file.write_text(
                text.replace("desktop:", "desktop:\n  launch_to_tray: 是", 1) if "desktop:" in text
                else text + "\ndesktop:\n  launch_to_tray: 是\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                SettingsRepository(settings_file, user_root=paths.user_root).load_desktop()

    def test_form_loads_and_collects_launch_to_tray(self):
        form = RuntimePreferencesForm([], DesktopSettings(launch_to_tray=True))
        self.assertTrue(form.launch_to_tray.isChecked())
        self.assertTrue(form.values().launch_to_tray)
        form.launch_to_tray.setChecked(False)
        self.assertFalse(form.values().launch_to_tray)

    def test_form_defaults_to_off(self):
        form = RuntimePreferencesForm([], DesktopSettings())
        self.assertFalse(form.launch_to_tray.isChecked())
        self.assertFalse(form.values().launch_to_tray)

    def test_dock_visibility_helper_is_noop_off_darwin(self):
        from unittest import mock as _mock

        from airfare_monitor.desktop_app import application as app_module

        with _mock.patch.object(app_module.sys, "platform", "linux"):
            app_module._apply_dock_visibility(False)  # 不得抛异常、不得碰 AppKit
            app_module._apply_dock_visibility(True)

    def test_notifications_save_with_none_keeps_persisted_tray_mode(self):
        """通知设置页保存转发 emit(None)：托盘模式必须回读持久化值而非误关。"""
        from airfare_monitor.desktop_app import application as app_module
        from airfare_monitor.desktop_app.preferences import PreferencesManager

        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            preferences = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root)
            )
            preferences.save(replace(preferences.load(), launch_to_tray=True))
            self.assertTrue(app_module._resolve_tray_mode(None, preferences))
            self.assertFalse(
                app_module._resolve_tray_mode(DesktopSettings(launch_to_tray=False), preferences)
            )

    def test_onboarding_form_hides_tray_mode_row(self):
        from airfare_monitor.ui.preferences import RuntimePreferencesForm as _Form

        hidden = _Form([], DesktopSettings(), show_launch_to_tray=False)
        self.assertIsNone(hidden.launch_to_tray.parentWidget(), "onboarding 表单不得包含托盘模式开关")
        shown = _Form([], DesktopSettings())
        self.assertIsNotNone(shown.launch_to_tray.parentWidget())


if __name__ == "__main__":
    unittest.main()
