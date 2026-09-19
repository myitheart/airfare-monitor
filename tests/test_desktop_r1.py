from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.collector import QunarBrowserSession
from airfare_monitor.config import BrowserSettings
from airfare_monitor.desktop_app.browser_detector import BrowserCandidate, BrowserDetector
from airfare_monitor.desktop_app.controller import DesktopController
from airfare_monitor.desktop_app.preferences import PreferencesError, PreferencesManager
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.desktop_app.settings_repository import DesktopSettings, SettingsRepository
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.ui.main_window import MainWindow
from airfare_monitor.ui.onboarding import OnboardingDialog


ROOT = Path(__file__).resolve().parents[1]


class DesktopR1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_roundtrip_updates_the_core_browser_and_schedule(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            chrome = Path(temp) / "chrome.exe"
            chrome.touch()
            autostart = _FakeAutostart()
            manager = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root),
                browser_detector=_FakeBrowserDetector([BrowserCandidate("chrome", chrome, "1.2.3")]),
                autostart=autostart,
            )
            manager.refresh_browsers()

            saved = manager.save(
                DesktopSettings(
                    browser_kind="chrome",
                    browser_path=str(chrome),
                    interval_minutes=60,
                    show_browser=False,
                    desktop_notifications=False,
                    autostart=True,
                    onboarding_completed=True,
                )
            )

            core = manager.repository.load_core()
            self.assertEqual(core.schedule.interval_minutes, 60)
            self.assertTrue(core.browser.headless)
            self.assertEqual(core.browser.executable_path, chrome.resolve())
            self.assertTrue(saved.onboarding_completed)
            self.assertTrue(autostart.enabled)

    def test_completed_onboarding_cannot_be_saved_without_a_browser(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            manager = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root),
                browser_detector=_FakeBrowserDetector([]),
                autostart=_FakeAutostart(),
            )
            manager.refresh_browsers()
            with self.assertRaisesRegex(PreferencesError, "Chrome 或 Edge"):
                manager.save(DesktopSettings(onboarding_completed=True))
            self.assertFalse(manager.load().onboarding_completed)

    def test_removed_saved_browser_is_not_silently_replaced(self):
        edge = BrowserCandidate("edge", Path("C:/Program Files/Edge/msedge.exe"), "130")
        selected = BrowserDetector.select(
            [edge],
            preferred_kind="chrome",
            preferred_path="C:/Program Files/Chrome/chrome.exe",
        )
        self.assertIsNone(selected)

    def test_settings_are_rolled_back_when_autostart_registration_fails(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            chrome = Path(temp) / "chrome.exe"
            chrome.touch()
            manager = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root),
                browser_detector=_FakeBrowserDetector([BrowserCandidate("chrome", chrome, None)]),
                autostart=_FailingAutostart(),
            )
            manager.refresh_browsers()
            with self.assertRaisesRegex(RuntimeError, "registry unavailable"):
                manager.save(
                    DesktopSettings(
                        browser_kind="chrome",
                        browser_path=str(chrome),
                        autostart=True,
                        onboarding_completed=True,
                    )
                )
            self.assertFalse(manager.load().onboarding_completed)

    def test_system_settings_page_uses_the_same_persistence_workflow(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            edge = Path(temp) / "msedge.exe"
            edge.touch()
            candidate = BrowserCandidate("edge", edge, "130")
            manager = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root),
                browser_detector=_FakeBrowserDetector([candidate]),
                autostart=_FakeAutostart(),
            )
            manager.refresh_browsers()
            window = MainWindow(
                DesktopController(RouteRepository(paths.routes_path)),
                AirportCatalog.load(paths.resource_root / "airports.zh.json"),
                [candidate],
                manager,
                on_run_now=lambda: False,
            )
            self.assertTrue(all(not button.icon().isNull() for button in window.nav_buttons))
            self.assertTrue(all(button.iconSize().width() == 22 for button in window.nav_buttons))
            self.assertFalse(window.system.save_button.icon().isNull())
            self.assertEqual(window.system.service_health.objectName(), "systemHealthCard")
            self.assertEqual(window.system.storage_health.property("accent"), "blue")
            self.assertEqual(window.system.query_health.property("accent"), "violet")
            self.assertIsNotNone(window.system.form.redetect_button)
            self.assertEqual(window.system.form.redetect_button.objectName(), "redetectButton")
            window.system.set_runtime("等待下轮", "下一次自动查询将在 30 分钟后开始。")
            self.assertEqual(window.system.runtime_label.text(), "运行状态：等待下轮")
            self.assertIn("30 分钟", window.system.runtime_detail_label.text())
            window.dashboard.set_runtime("等待下轮", "下次自动查询：09-17 16:12")
            self.assertIn("等待下轮", window.dashboard.runtime_detail_label.text())
            self.assertIn("下次自动查询", window.dashboard.runtime_detail_label.text())
            self.assertEqual((window.dashboard.pause_button.width(), window.dashboard.pause_button.height()), (112, 36))
            self.assertEqual((window.dashboard.run_button.width(), window.dashboard.run_button.height()), (116, 36))
            self.assertEqual((window.dashboard.add_button.width(), window.dashboard.add_button.height()), (118, 36))
            emitted: list[DesktopSettings] = []
            window.runtime_settings_saved.connect(emitted.append)
            window.system.form.interval_combo.setCurrentIndex(
                window.system.form.interval_combo.findData(60)
            )
            window.system.form.show_browser.setChecked(False)
            with patch("airfare_monitor.ui.main_window.QMessageBox.information"):
                window.system._save_settings()
            self.assertEqual(len(emitted), 1)
            self.assertEqual(manager.load().interval_minutes, 60)
            self.assertFalse(manager.load().show_browser)
            window.close()

    def test_onboarding_requires_browser_and_persists_selected_values(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            edge = Path(temp) / "msedge.exe"
            edge.touch()
            manager = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root),
                browser_detector=_FakeBrowserDetector([BrowserCandidate("edge", edge, "130")]),
                autostart=_FakeAutostart(),
            )
            dialog = OnboardingDialog(manager)
            self.assertTrue(dialog.finish_button.isEnabled())
            dialog.form.interval_combo.setCurrentIndex(dialog.form.interval_combo.findData(120))
            dialog.form.show_browser.setChecked(False)
            dialog._complete()
            self.assertIsNotNone(dialog.saved_settings)
            saved = manager.load()
            self.assertEqual(saved.browser_kind, "edge")
            self.assertEqual(saved.interval_minutes, 120)
            self.assertFalse(saved.show_browser)
            self.assertTrue(saved.onboarding_completed)
            dialog.close()

    def test_onboarding_finish_is_disabled_when_no_browser_is_detected(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            manager = PreferencesManager(
                SettingsRepository(paths.settings_path, user_root=paths.user_root),
                browser_detector=_FakeBrowserDetector([]),
                autostart=_FakeAutostart(),
            )
            dialog = OnboardingDialog(manager)
            self.assertFalse(dialog.finish_button.isEnabled())
            self.assertIn("Chrome 或 Edge", dialog.finish_button.toolTip())
            dialog.close()

    def test_collection_uses_the_browser_selected_in_desktop_settings(self):
        with TemporaryDirectory() as temp:
            browser_path = Path(temp) / "browser.exe"
            captured: dict[str, object] = {}

            class FakeOptions:
                def __init__(self, *, read_file: bool):
                    self.read_file = read_file

                def set_browser_path(self, value: str):
                    captured["browser_path"] = value

                def set_local_port(self, value: int):
                    captured["port"] = value

                def set_user_data_path(self, value: str):
                    captured["profile"] = value

                def headless(self, value: bool):
                    captured["headless"] = value

            class FakeBrowser:
                def __init__(self, *, addr_or_opts: object):
                    captured["options"] = addr_or_opts
                    self.latest_tab = SimpleNamespace(
                        set=SimpleNamespace(timeouts=lambda **kwargs: captured.update(kwargs))
                    )

                def quit(self):
                    captured["quit"] = True

            settings = BrowserSettings(
                headless=True,
                user_data_path=Path(temp) / "profile",
                local_port=9333,
                page_load_timeout_seconds=45,
                search_completion_timeout_seconds=75,
                restart_after_consecutive_failures=2,
                search_url_template="https://example.test",
                executable_path=browser_path,
            )
            module = SimpleNamespace(Chromium=FakeBrowser, ChromiumOptions=FakeOptions)
            with patch.dict(sys.modules, {"DrissionPage": module}):
                session = QunarBrowserSession(settings)
                session.start()
                session.close()

            self.assertEqual(captured["browser_path"], str(browser_path))
            self.assertTrue(captured["headless"])
            self.assertEqual(captured["profile"], str(settings.user_data_path))
            self.assertTrue(captured["quit"])


class _FakeAutostart:
    def __init__(self):
        self.enabled = False

    def is_enabled(self) -> bool:
        return self.enabled

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled


class _FailingAutostart(_FakeAutostart):
    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            raise RuntimeError("registry unavailable")
        self.enabled = False


class _FakeBrowserDetector:
    def __init__(self, candidates: list[BrowserCandidate]):
        self.candidates = candidates

    def detect(self, *, preferred_path=None):
        return list(self.candidates)

    select = staticmethod(BrowserDetector.select)


def _paths(root: str) -> AppPaths:
    discovered = AppPaths.discover(user_root=root)
    fields = {name: getattr(discovered, name) for name in discovered.__dataclass_fields__}
    fields["resource_root"] = ROOT / "resources"
    return AppPaths(**fields)
