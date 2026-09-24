from __future__ import annotations

import os
import smtplib
import tempfile
import time as wall_time
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.mail_errors import friendly_mail_error
from airfare_monitor.desktop_app.diagnostics import build_diagnostics
from airfare_monitor.desktop_app.mail_profile import MailProfile, MailProfileRepository
from airfare_monitor.desktop_app.notification_policy import alert_for_event
from airfare_monitor.desktop_app.events import (
    CycleFinished, MailDeliveryFailed, ManualAttentionRequested,
    VerificationBrowserOpened,
)
from airfare_monitor.desktop_app.monitor_coordinator import MonitorCoordinator
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.desktop_app.preferences import PreferencesManager
from airfare_monitor.desktop_app.settings_repository import DesktopSettings, SettingsRepository
from airfare_monitor.models import EtdWindow, LegConfig, LegResult, LegStatus, RunReport, RunStatus
from airfare_monitor.service import MonitorService
from airfare_monitor.storage import SQLiteStore
from airfare_monitor.ui.notifications_page import NotificationsPage


ROOT = Path(__file__).resolve().parents[1]


class DesktopR3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mail_profile_keeps_secret_out_of_yaml_and_cli_mail_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = MailProfileRepository(paths.settings_path, user_root=paths.user_root)
            profile = _profile()
            repository.save(profile)

            self.assertEqual(repository.load(), profile)
            text = paths.settings_path.read_text(encoding="utf-8")
            self.assertNotIn("example-authorization-code", text)
            self.assertIn("desktop_mail:", text)
            self.assertFalse(SettingsRepository(paths.settings_path, user_root=paths.user_root).load_core().mail.enabled)

    def test_profile_rejects_invalid_addresses_and_security(self):
        with self.assertRaisesRegex(ValueError, "收件"):
            replace(_profile(), recipients=("bad-address",)).validate()
        with self.assertRaisesRegex(ValueError, "安全"):
            MailProfile(security="plain").validate()

    def test_notification_page_is_real_and_does_not_send_on_load(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            preferences = PreferencesManager(settings)
            repository = MailProfileRepository(paths.settings_path, user_root=paths.user_root)
            with patch("airfare_monitor.ui.notifications_page.send_test_message") as sender:
                page = NotificationsPage(preferences, repository)
                self.assertEqual(page.test_button.text(), "发送测试邮件并保存")
                self.assertEqual(page.save_button.text(), "保存设置")
                self.assertEqual(page.reset_button.text(), "重置")
                self.assertEqual(page.password.echoMode(), page.password.EchoMode.Password)
                page.desktop_toggle.setChecked(False)
                page.mail_toggle.setChecked(True)
                self.assertEqual(page.desktop_state.text(), "已关闭")
                self.assertEqual(page.mail_state.text(), "已开启")
                page.show()
                self.app.processEvents()
                for toggle in (page.desktop_toggle, page.mail_toggle):
                    toggle.setChecked(False)
                    QTest.mouseClick(
                        toggle,
                        Qt.MouseButton.LeftButton,
                        pos=toggle.rect().center(),
                    )
                    self.assertTrue(toggle.isChecked())
                    QTest.mouseClick(
                        toggle,
                        Qt.MouseButton.LeftButton,
                        pos=QPoint(toggle.width() - 4, toggle.height() // 2),
                    )
                    self.assertFalse(toggle.isChecked())
                sender.assert_not_called()
                page.close()

    def test_mail_page_saves_secret_only_through_credential_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            repository = MailProfileRepository(paths.settings_path, user_root=paths.user_root)
            secrets = _MemoryCredentials()
            page = NotificationsPage(PreferencesManager(settings), repository, secrets)
            page._commit(_profile(), "example-authorization-code")
            self.assertEqual(secrets.get_secret("sender@example.com"), "example-authorization-code")
            self.assertNotIn("example-authorization-code", paths.settings_path.read_text(encoding="utf-8"))
            page.close()

    def test_explicit_test_runs_in_worker_then_saves_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = MailProfileRepository(paths.settings_path, user_root=paths.user_root)
            page = NotificationsPage(
                PreferencesManager(SettingsRepository(paths.settings_path, user_root=paths.user_root)),
                repository,
                _MemoryCredentials(),
            )
            page.mail_toggle.setChecked(True)
            page.host.setText("smtp.example.com")
            page.username.setText("sender@example.com")
            page.sender.setText("sender@example.com")
            page.recipients.setText("recipient@example.com")
            page.password.setText("test-authorization-code")
            with patch("airfare_monitor.ui.notifications_page.send_test_message") as sender:
                page._test_mail()
                for _ in range(150):
                    self.app.processEvents()
                    if page._thread is None and repository.load().enabled:
                        break
                    wall_time.sleep(0.01)
                sender.assert_called_once()
                self.assertTrue(repository.load().enabled)
                self.assertIn("测试成功", page.status.text())
            page.close()

    def test_alert_policy_is_quiet_on_success_and_uses_actionable_pages(self):
        leg = _leg()
        now = datetime(2026, 9, 15, 10)
        success = LegResult(leg, LegStatus.SUCCESS, now, completed_response=True)
        report = RunReport("run", now, now, RunStatus.SUCCESS, [success], set())
        self.assertIsNone(alert_for_event(CycleFinished(report, "report.xlsx", 1)))
        report.threshold_confirmed_leg_ids.add(leg.id)
        low_price = alert_for_event(
            CycleFinished(report, "report.xlsx", 1),
            low_price_details=[{
                "route_code": "SHA → XMN",
                "actual_price_cny": "880",
                "threshold_price_cny": "1000",
                "savings_cny": "120",
            }],
        )
        self.assertEqual(low_price.target_page, 0)
        self.assertIn("SHA → XMN", low_price.title)
        self.assertIn("¥880", low_price.message)
        self.assertIn("¥1,000", low_price.message)
        self.assertIsNone(alert_for_event(
            CycleFinished(report, "report.xlsx", 1), low_price_details=[]
        ))
        self.assertEqual(alert_for_event(ManualAttentionRequested(leg.id, "sensitive" )).target_page, 4)
        self.assertEqual(alert_for_event(MailDeliveryFailed("AuthenticationError")).target_page, 3)

    def test_mail_failure_does_not_discard_saved_prices_or_excel(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings = SettingsRepository(paths.settings_path, user_root=paths.user_root).load_core()
            leg = _leg()
            sink = _Sink()
            service = MonitorService(
                [leg], settings, browser=_Browser(), event_sink=sink,
                now=lambda: datetime(2026, 9, 15, 10),
                mail_delivery=lambda report, workbook: (_ for _ in ()).throw(RuntimeError("secret response")),
            )
            report, workbook = service.run_once(send_email=True)
            self.assertEqual(report.status, RunStatus.SUCCESS)
            self.assertTrue(Path(workbook).is_file())
            self.assertEqual(sink.mail_failures, ["RuntimeError"])
            self.assertIsNotNone(service.store.latest_run())

    def test_coordinator_uses_desktop_mail_without_enabling_cli_env_mail(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg()])
            MailProfileRepository(paths.settings_path, user_root=paths.user_root).save(_profile())
            coordinator = MonitorCoordinator(
                paths,
                service_factory=lambda legs, settings, sink, delay: MonitorService(
                    legs, settings, browser=_Browser(), event_sink=sink,
                    sleep=delay, now=lambda: datetime(2026, 9, 15, 10),
                ),
                now=lambda: datetime(2026, 9, 15, 10),
                jitter=lambda low, high: 0,
            )
            with patch("airfare_monitor.desktop_app.monitor_coordinator.CredentialStore", return_value=_MemoryCredentials({"sender@example.com": "test-code"})), patch(
                "airfare_monitor.desktop_app.monitor_coordinator.send_report_with_credentials"
            ) as send:
                coordinator._execute_cycle(None)
                send.assert_called_once()
            self.assertIsNotNone(coordinator._next_schedule())

    def test_verification_opens_same_isolated_profile_visible_without_changing_setting(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            settings_repo = SettingsRepository(paths.settings_path, user_root=paths.user_root)
            settings_repo.save_desktop(replace(settings_repo.load_desktop(), show_browser=False))
            RouteRepository(paths.routes_path).save([_leg()])
            coordinator = MonitorCoordinator(paths)
            events: list[object] = []
            coordinator.subscribe(events.append)
            opened: list[_VerificationBrowser] = []

            def factory(settings):
                browser = _VerificationBrowser(settings)
                opened.append(browser)
                return browser

            with patch("airfare_monitor.desktop_app.monitor_coordinator.QunarBrowserSession", side_effect=factory):
                coordinator._open_verification_browser("r3-leg")
                self.assertTrue(any(isinstance(event, VerificationBrowserOpened) for event in events))
                self.assertEqual(opened[0].settings.user_data_path, paths.browser_profile)
                self.assertFalse(opened[0].settings.headless)
                self.assertEqual(len(opened[0].tab.urls), 1)
                coordinator._close_verification_browser()
                self.assertTrue(opened[0].closed)
            self.assertEqual(
                SettingsRepository(paths.settings_path, user_root=paths.user_root).load_core().browser.headless,
                True,
            )

    def test_verification_keeps_visible_browser_when_page_load_times_out(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg()])
            coordinator = MonitorCoordinator(paths)
            events: list[object] = []
            coordinator.subscribe(events.append)
            browser = _VerificationBrowser(SettingsRepository(paths.settings_path, user_root=paths.user_root).load_core().browser)
            browser.tab = _TimeoutTab()
            with patch("airfare_monitor.desktop_app.monitor_coordinator.QunarBrowserSession", return_value=browser):
                coordinator._open_verification_browser("r3-leg")
                self.assertTrue(any(isinstance(event, VerificationBrowserOpened) for event in events))
                self.assertFalse(browser.closed)
                coordinator._close_verification_browser()

    def test_smtp_error_message_never_echoes_server_response(self):
        exc = smtplib.SMTPAuthenticationError(535, b"user@example.com secret-value")
        result = friendly_mail_error(exc)
        self.assertIn("认证失败", result)
        self.assertNotIn("secret-value", result)
        self.assertNotIn("user@example.com", result)

    def test_diagnostics_excludes_event_message_and_personal_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            store = SQLiteStore(paths.database_path)
            store.initialize()
            store.record_app_event(
                event_type="mail_failed", severity="warning", leg_id="secret-route",
                message="user@example.com authorization-token-sensitive",
                details={"cookie": "private-cookie"},
            )
            payload = build_diagnostics(
                SettingsRepository(paths.settings_path, user_root=paths.user_root).load_desktop(),
                store, enabled_routes=1,
            )
            serialized = str(payload)
            for forbidden in (
                "user@example.com", "authorization-token-sensitive", "private-cookie",
                "secret-route", str(paths.user_root),
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(payload["recent_events"][0]["event_type"], "mail_failed")


class _Sink:
    def __init__(self):
        self.mail_failures: list[str] = []

    def on_cycle_started(self, *args): pass
    def on_leg_started(self, *args): pass
    def on_leg_finished(self, *args): pass
    def on_cycle_finished(self, *args): pass
    def on_mail_failed(self, category): self.mail_failures.append(category)


class _Browser:
    def collect(self, leg, now):
        return LegResult(leg, LegStatus.SUCCESS, now(), completed_response=True)
    def close(self): pass
    def restart(self): pass


class _MemoryCredentials:
    def __init__(self, values=None):
        self.values = dict(values or {})
    def get_secret(self, username): return self.values.get(username)
    def has_secret(self, username): return username in self.values
    def save_secret(self, username, secret): self.values[username] = secret
    def delete_secret(self, username): self.values.pop(username, None)


class _Tab:
    def __init__(self): self.urls = []
    def get(self, url, timeout): self.urls.append(url)


class _TimeoutTab(_Tab):
    def get(self, url, timeout):
        self.urls.append(url)
        raise TimeoutError("challenge interrupted navigation")


class _VerificationBrowser:
    def __init__(self, settings):
        self.settings = settings
        self.tab = _Tab()
        self.closed = False
    def start(self): pass
    def close(self): self.closed = True


def _profile() -> MailProfile:
    return MailProfile(
        enabled=True, smtp_host="smtp.example.com", smtp_port=465,
        security="ssl", username="sender@example.com", sender="sender@example.com",
        recipients=("recipient@example.com",), attach_excel=True,
    )


def _leg() -> LegConfig:
    return LegConfig(
        id="r3-leg", enabled=True, origin_airport_iata="SHA", destination_airport_iata="XMN",
        departure_date=date(2026, 10, 1), etd_window=EtdWindow(time(0), time(23, 59)),
        direct_only=True, expected_total_price_cny=Decimal("1000"), top_n=10,
        adult_count=1, child_count=0, cabin_class="economy",
        origin_name_zh="上海虹桥", destination_name_zh="厦门高崎",
    )


def _paths(root: str) -> AppPaths:
    found = AppPaths.discover(user_root=root)
    values = {name: getattr(found, name) for name in found.__dataclass_fields__}
    values["resource_root"] = ROOT / "resources"
    return AppPaths(**values)
