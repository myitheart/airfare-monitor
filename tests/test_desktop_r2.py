from __future__ import annotations

import os
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.desktop_app.controller import DesktopController
from airfare_monitor.desktop_app.event_journal import AppEventJournal
from airfare_monitor.desktop_app.events import CycleFinished, FatalError, RoutesExpired
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.desktop_app.view_data import load_dashboard_data
from airfare_monitor.models import (
    EtdWindow, FlightSnapshot, LegConfig, LegResult, LegStatus, RunReport, RunStatus,
)
from airfare_monitor.storage import SQLiteStore
from airfare_monitor.ui.app_icon import application_icon
from airfare_monitor.ui.dashboard_page import DashboardPage
from airfare_monitor.ui.flight_results_page import FlightResultsPage, filter_and_sort_candidates
from airfare_monitor.ui.history_page import HistoryPage, PriceChart, _chart_tooltip_text, price_segments
from airfare_monitor.ui.main_window import RoutesPage
from airfare_monitor.ui.route_wizard import RouteWizard


ROOT = Path(__file__).resolve().parents[1]


class DesktopR2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_price_chart_breaks_the_line_at_failed_queries(self):
        rows = [
            {"status": "success", "minimum_total_price_cny": "1500"},
            {"status": "success", "minimum_total_price_cny": "1450"},
            {"status": "failed", "minimum_total_price_cny": None},
            {"status": "success", "minimum_total_price_cny": "1420"},
            {"status": "manual_attention", "minimum_total_price_cny": None},
        ]
        self.assertEqual(
            price_segments(rows),
            [[(0, Decimal("1500")), (1, Decimal("1450"))], [(3, Decimal("1420"))]],
        )

    def test_price_chart_tooltip_shows_capture_time_and_price(self):
        row = {
            "captured_at": "2026-09-17T19:27:35",
            "status": "success",
            "minimum_total_price_cny": "3273",
        }
        self.assertEqual(
            _chart_tooltip_text(row, Decimal("3273")),
            "采集时间：2026-09-17 19:27:35\n含税总价：¥3,273",
        )

    def test_price_chart_tooltip_shows_failed_query_status(self):
        row = {
            "captured_at": "2026-09-17T15:37:00",
            "status": "manual_attention",
            "minimum_total_price_cny": None,
        }
        self.assertEqual(
            _chart_tooltip_text(row, None),
            "采集时间：2026-09-17 15:37:00\n查询结果：需要人工处理",
        )

    def test_price_chart_hover_uses_nearest_point_within_hit_radius(self):
        chart = PriceChart()
        first = {"status": "success", "captured_at": "2026-09-17T19:00:00"}
        second = {"status": "success", "captured_at": "2026-09-17T19:30:00"}
        chart._hit_points = [
            (0, QPointF(20, 20), first, Decimal("3200")),
            (1, QPointF(40, 20), second, Decimal("3300")),
        ]
        self.assertEqual(chart._nearest_hit(QPointF(38, 22))[0], 1)
        self.assertIsNone(chart._nearest_hit(QPointF(70, 70)))

    def test_application_icon_contains_branded_tray_sizes(self):
        icon = application_icon()
        self.assertFalse(icon.isNull())
        available = {(size.width(), size.height()) for size in icon.availableSizes()}
        self.assertIn((16, 16), available)
        self.assertIn((32, 32), available)
        pixmap = icon.pixmap(64, 64)
        self.assertFalse(pixmap.isNull())
        center = pixmap.toImage().pixelColor(32, 32)
        self.assertGreater(center.alpha(), 0)
        self.assertGreater(center.blue(), center.red())

    def test_route_cards_show_existing_actions_and_capacity(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            controller = DesktopController(RouteRepository(paths.routes_path))
            catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
            page = RoutesPage(controller, catalog)
            page.refresh([_route("route-1")])
            self.assertEqual(page.capacity.text(), "已启用 1 / 10 个航程")
            self.assertEqual(page.capacity_bar.value(), 1)
            self.assertEqual(len(page.cards), 1)
            self.assertEqual(page.cards[0].objectName(), "managedRouteCard")
            self.assertGreaterEqual(page.cards[0].minimumHeight(), 363)
            buttons = page.cards[0].findChildren(QPushButton)
            self.assertEqual(
                {button.text() for button in buttons},
                {"编辑", "暂停", "复制", "删除", "查看候选", "在去哪儿打开"},
            )
            labels = {label.text() for label in page.cards[0].findChildren(QLabel)}
            self.assertIn("PVG", labels)
            self.assertIn("KUL", labels)
            self.assertIn("国际/跨境 · 去哪儿", labels)
            page.close()

    def test_expired_route_is_labeled_and_guides_user_to_edit_date(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            controller = DesktopController(
                RouteRepository(paths.routes_path),
                today=lambda: date(2026, 9, 21),
            )
            catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
            page = RoutesPage(controller, catalog)
            expired = replace(
                _route("expired-route"),
                enabled=False,
                departure_date=date(2026, 9, 20),
            )

            page.refresh([expired])

            labels = {label.text() for label in page.cards[0].findChildren(QLabel)}
            buttons = {button.text() for button in page.cards[0].findChildren(QPushButton)}
            self.assertIn("已过期", labels)
            self.assertIn("出发日期已过，监控已自动暂停", labels)
            self.assertIn("编辑日期", buttons)
            self.assertNotIn("启用", buttons)
            page.close()

    def test_controller_rejects_enabling_an_expired_route(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = RouteRepository(paths.routes_path)
            expired = replace(
                _route("expired-route"),
                enabled=False,
                departure_date=date(2026, 9, 20),
            )
            repository.save([expired])
            controller = DesktopController(repository, today=lambda: date(2026, 9, 21))

            with self.assertRaisesRegex(ValueError, "编辑航程日期"):
                controller.toggle_route(expired.id, True)

            self.assertFalse(repository.load()[0].enabled)

    def test_candidate_filter_supports_price_time_connection_and_search(self):
        rows = [
            _candidate_row("MU100", "MU", "900", "2026-10-01T08:00:00", True, 300),
            _candidate_row("CZ200", "CZ", "700", "2026-10-01T14:00:00", False, 480),
            _candidate_row("MH300", "MH", "800", "2026-10-01T19:00:00", True, 360),
        ]
        filtered = filter_and_sort_candidates(
            rows,
            search="CZ",
            connection="transfer",
            period="afternoon",
            below_threshold=Decimal("750"),
            sort_by="price",
        )
        self.assertEqual([item["flight_codes"][0] for item in filtered], ["CZ200"])
        self.assertEqual(
            [item["flight_codes"][0] for item in filter_and_sort_candidates(rows, sort_by="price")],
            ["CZ200", "MH300", "MU100"],
        )

    def test_flight_results_page_reads_all_locally_stored_candidates(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            store.initialize()
            route = _route("route-1")
            captured = datetime(2026, 9, 15, 10, 30)
            cheapest = _flight("candidate-1", "MU100", Decimal("900"), captured)
            alternative = _flight("candidate-2", "CZ200", Decimal("980"), captured)
            result = LegResult(
                route,
                LegStatus.SUCCESS,
                captured,
                flights=[cheapest],
                candidate_flights=[cheapest, alternative],
                completed_response=True,
                observed_count=5,
                eligible_count=2,
            )
            store.save_report(
                RunReport("run-1", captured, captured, RunStatus.SUCCESS, [result], set())
            )
            saved = store.latest_flight_candidates(route.id)
            self.assertEqual(saved["stored_count"], 2)
            self.assertEqual(saved["flights"][1]["flight_codes"], ["CZ200"])
            page = FlightResultsPage(store, on_back=lambda: None)
            page.show_route(route)
            self.assertEqual(page.table.rowCount(), 2)
            self.assertEqual(page.result_count.text(), "2 条")
            page.close()

    def test_expanded_candidates_expire_but_historical_top_ten_remain(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            store.initialize()
            route = _route("route-1")
            old_at = datetime(2026, 9, 1, 10, 30)
            candidates = [
                _flight(f"candidate-{index}", f"MU{index:03d}", Decimal(900 + index), old_at)
                for index in range(12)
            ]
            result = LegResult(
                route,
                LegStatus.SUCCESS,
                old_at,
                flights=candidates[:10],
                candidate_flights=candidates,
                completed_response=True,
                eligible_count=12,
            )
            store.save_report(
                RunReport("run-old", old_at, old_at, RunStatus.SUCCESS, [result], set())
            )

            deleted = store.prune_candidate_details(7, now=datetime(2026, 9, 15, 10, 30))

            self.assertEqual(deleted, 2)
            connection = store.connect()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM flight_snapshots WHERE run_id = 'run-old'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(count, 10)

    def test_dashboard_data_uses_persisted_cny_totals_and_redacted_events(self):
        route = _route("route-1")
        store = _FakeDashboardStore()
        data = load_dashboard_data(store, [route], now=datetime(2026, 9, 14, 18))
        self.assertEqual(data.today_minimum_cny, Decimal("1380"))
        self.assertEqual(data.today_minimum_leg_id, route.id)
        self.assertEqual(data.today_minimum_captured_at, datetime(2026, 9, 14, 12))
        self.assertEqual(data.routes[route.id].minimum_total_cny, Decimal("1420"))
        self.assertEqual(data.routes[route.id].change_cny, Decimal("-60"))
        self.assertTrue(data.routes[route.id].threshold_confirmed)
        self.assertEqual(data.attention_count, 0)
        self.assertEqual(data.latest_run_duration_seconds, 75)

    def test_dashboard_low_price_card_identifies_route_price_and_opens_candidates(self):
        route = _route("route-1")
        data = load_dashboard_data(
            _FakeDashboardStore(),
            [route],
            now=datetime(2026, 9, 14, 18),
        )
        opened: list[LegConfig] = []
        page = DashboardPage(
            lambda: None,
            lambda: None,
            lambda: None,
            lambda: None,
            opened.append,
        )
        page.refresh([route])
        page.set_data(data)

        labels = {label.text() for label in page.findChildren(QLabel)}
        self.assertIn("PVG → KUL", labels)
        self.assertIn("¥1,380", labels)
        self.assertIn("心理价 ¥1,500 · 低于心理价 ¥120", labels)
        self.assertIn("低价命中", labels)
        self.assertIn("PVG → KUL · 09-14 12:00", labels)
        self.assertEqual(page.findChild(QLabel, "lowPricePill").text(), "低价命中")
        action = page.findChild(QPushButton, "lowPriceEventAction")
        self.assertIsNotNone(action)
        action.click()
        self.assertEqual([item.id for item in opened], [route.id])
        page.close()

    def test_event_journal_does_not_persist_raw_fatal_error_text(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            journal = AppEventJournal(store)
            journal.initialize()
            journal.record(FatalError("CollectionError", "session_parameter=sensitive-marker"))
            events = store.recent_app_events()
            self.assertEqual(len(events), 1)
            self.assertNotIn("sensitive-marker", events[0]["message"])
            self.assertEqual(events[0]["severity"], "error")

    def test_event_journal_records_expired_route_without_deleting_history(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            journal = AppEventJournal(store)
            journal.initialize()
            expired = replace(_route("expired-route"), departure_date=date(2026, 9, 20))

            journal.record(RoutesExpired((expired,), datetime(2026, 9, 21, 0, 1), 0))

            events = store.recent_app_events()
            self.assertEqual(events[0]["event_type"], "routes_expired")
            self.assertIn("监控已自动暂停", events[0]["message"])
            self.assertEqual(events[0]["leg_id"], expired.id)

    def test_low_price_events_include_route_and_price_and_suppress_recent_duplicates(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            journal = AppEventJournal(store)
            journal.initialize()
            route = _route("route-1")
            first_at = datetime(2026, 9, 14, 12)

            first = _confirmed_report(route, "run-1", Decimal("1380"), first_at)
            journal.record(CycleFinished(first, "first.xlsx", 1))

            low_events = [
                event for event in store.recent_app_events()
                if event["event_type"] == "low_price_confirmed"
            ]
            self.assertEqual(len(low_events), 1)
            self.assertEqual(low_events[0]["leg_id"], route.id)
            self.assertIn("PVG → KUL", low_events[0]["message"])
            self.assertIn("¥1,380", low_events[0]["message"])
            self.assertEqual(low_events[0]["details"]["threshold_price_cny"], "1500")
            self.assertEqual(len(journal.low_price_alert_details("run-1")), 1)

            duplicate = _confirmed_report(
                route,
                "run-2",
                Decimal("1380"),
                first_at.replace(hour=13),
            )
            journal.record(CycleFinished(duplicate, "duplicate.xlsx", 1))
            low_events = [
                event for event in store.recent_app_events()
                if event["event_type"] == "low_price_confirmed"
            ]
            self.assertEqual(len(low_events), 1)
            self.assertEqual(journal.low_price_alert_details("run-2"), ())

            lower = _confirmed_report(
                route,
                "run-3",
                Decimal("1320"),
                first_at.replace(hour=14),
            )
            journal.record(CycleFinished(lower, "lower.xlsx", 1))
            low_events = [
                event for event in store.recent_app_events()
                if event["event_type"] == "low_price_confirmed"
            ]
            self.assertEqual(len(low_events), 2)
            self.assertEqual(low_events[0]["details"]["savings_cny"], "180")
            self.assertEqual(len(journal.low_price_alert_details("run-3")), 1)

            aged = _confirmed_report(
                route,
                "run-4",
                Decimal("1380"),
                datetime(2026, 9, 15, 15),
            )
            journal.record(CycleFinished(aged, "aged.xlsx", 1))
            low_events = [
                event for event in store.recent_app_events()
                if event["event_type"] == "low_price_confirmed"
            ]
            self.assertEqual(len(low_events), 3)
            self.assertEqual(len(journal.low_price_alert_details("run-4")), 1)

    def test_route_wizard_saves_period_passengers_cabin_and_paused_choice(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            controller = DesktopController(RouteRepository(paths.routes_path))
            catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
            wizard = RouteWizard(catalog, controller)
            wizard.origin_picker.set_record(catalog.by_iata("PVG"))
            wizard.destination_picker.set_record(catalog.by_iata("KUL"))
            wizard.departure_period.setCurrentIndex(2)
            wizard.adult_count.setValue(2)
            wizard.child_count.setValue(1)
            wizard.cabin_class.setCurrentIndex(wizard.cabin_class.findData("business"))
            wizard.stack.setCurrentIndex(2)
            wizard._update_step()
            self.assertEqual(wizard.next_button.text(), "保存并开始监控")
            self.assertFalse(wizard.save_paused_button.isHidden())
            route = wizard._build_route(force_enabled=False)
            self.assertEqual(route.etd_window.start, time(12, 0))
            self.assertEqual(route.etd_window.end, time(18, 0))
            self.assertEqual(route.adult_count, 2)
            self.assertEqual(route.child_count, 1)
            self.assertEqual(route.cabin_class, "business")
            self.assertFalse(route.enabled)
            wizard.close()

    def test_history_page_restores_routes_without_querying_a_website(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "monitor.sqlite3")
            store.initialize()
            page = HistoryPage(store, open_latest_report=lambda: None, outputs_dir=Path(temp))
            page.refresh([_route("route-1")])
            self.assertEqual(page.route_combo.count(), 1)
            self.assertEqual(page.records.rowCount(), 0)
            self.assertEqual(page.records.columnCount(), 3)
            page.set_period(24 * 7)
            self.assertEqual(page.period_summary.text(), "▣  最近 7 天")
            page.close()


class _FakeDashboardStore:
    def latest_leg_results(self, leg_ids):
        return [{
            "leg_id": "route-1",
            "status": "success",
            "minimum_total_price_cny": "1420",
            "previous_min_total_cny": "1480",
            "captured_at": "2026-09-14T17:30:00",
            "threshold_confirmed": 1,
        }]

    def history(self, *, since):
        return [
            {
                "leg_id": "route-1",
                "status": "success",
                "minimum_total_price_cny": "1380",
                "captured_at": "2026-09-14T12:00:00",
            },
            {
                "leg_id": "route-1",
                "status": "success",
                "minimum_total_price_cny": "1420",
                "captured_at": "2026-09-14T17:30:00",
            },
        ]

    def latest_successful_run(self):
        return {"finished_at": "2026-09-14T17:31:15"}

    def latest_run(self):
        return {
            "started_at": "2026-09-14T17:30:00",
            "finished_at": "2026-09-14T17:31:15",
        }

    def recent_app_events(self, *, limit):
        return [{
            "occurred_at": "2026-09-14T17:31:15",
            "event_type": "low_price_confirmed",
            "severity": "notice",
            "leg_id": "route-1",
            "message": "PVG → KUL 命中心理价：含税 ¥1,380，低于心理价 ¥120",
            "details": {
                "route_code": "PVG → KUL",
                "route_name": "上海浦东 → 吉隆坡",
                "actual_price_cny": "1380",
                "threshold_price_cny": "1500",
                "savings_cny": "120",
            },
        }]


def _route(identifier: str) -> LegConfig:
    return LegConfig(
        id=identifier,
        enabled=True,
        origin_airport_iata="PVG",
        destination_airport_iata="KUL",
        departure_date=date(2026, 10, 1),
        etd_window=EtdWindow(time(0, 0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=Decimal("1500"),
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        origin_name_zh="上海浦东",
        destination_name_zh="吉隆坡",
    )


def _confirmed_report(
    route: LegConfig,
    run_id: str,
    total: Decimal,
    captured_at: datetime,
) -> RunReport:
    flight = _flight(f"{run_id}-flight", "MU100", total, captured_at)
    result = LegResult(
        route,
        LegStatus.SUCCESS,
        captured_at,
        flights=[flight],
        completed_response=True,
    )
    return RunReport(
        run_id,
        captured_at,
        captured_at,
        RunStatus.SUCCESS,
        [result],
        {route.id},
    )


def _flight(
    signature: str,
    code: str,
    total: Decimal,
    captured: datetime,
) -> FlightSnapshot:
    return FlightSnapshot(
        flight_signature=signature,
        flight_codes=(code,),
        carrier_codes=(code[:2],),
        origin_airport_iata="PVG",
        destination_airport_iata="KUL",
        departure_date=date(2026, 10, 1),
        etd_local=datetime(2026, 10, 1, 8),
        eta_local=datetime(2026, 10, 1, 13, 30),
        duration_minutes=330,
        segment_count=1,
        is_direct=True,
        base_price_cny=total - Decimal("200"),
        tax_cny=Decimal("200"),
        total_price_cny=total,
        currency_code="CNY",
        remaining_seats="4",
        free_baggage_piece=1,
        free_baggage_weight="23kg",
        source_domain="example.test",
        captured_at=captured,
    )


def _candidate_row(
    code: str,
    carrier: str,
    total: str,
    departure: str,
    direct: bool,
    duration: int,
) -> dict[str, object]:
    return {
        "flight_signature": code,
        "flight_codes": [code],
        "carrier_codes": [carrier],
        "total_price_cny": total,
        "etd_local": departure,
        "eta_local": departure,
        "is_direct": direct,
        "duration_minutes": duration,
    }


def _paths(root: str) -> AppPaths:
    discovered = AppPaths.discover(user_root=root)
    fields = {name: getattr(discovered, name) for name in discovered.__dataclass_fields__}
    fields["resource_root"] = ROOT / "resources"
    return AppPaths(**fields)
