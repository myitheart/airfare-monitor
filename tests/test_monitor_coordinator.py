from __future__ import annotations

import threading
import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from airfare_monitor.app_paths import AppPaths
from airfare_monitor.desktop_app.events import (
    CoordinatorStateChanged,
    CycleFinished,
    NextRunScheduled,
    RoutesExpired,
)
from airfare_monitor.desktop_app.monitor_coordinator import MonitorCoordinator, calculate_next_run
from airfare_monitor.desktop_app.route_repository import RouteRepository
from airfare_monitor.models import EtdWindow, LegConfig, LegResult, LegStatus, RunReport, RunStatus
from airfare_monitor.scheduler import AlreadyRunningError, ProcessLock


ROOT = Path(__file__).resolve().parents[1]


class MonitorCoordinatorTests(unittest.TestCase):
    def test_next_run_uses_start_time_for_short_cycle(self):
        started = datetime(2026, 9, 10, 10, 0)
        finished = started + timedelta(minutes=2)
        self.assertEqual(
            calculate_next_run(started, finished, interval_minutes=30, jitter_seconds=10),
            datetime(2026, 9, 10, 10, 30, 10),
        )

    def test_next_run_keeps_five_minute_cooldown_after_long_cycle(self):
        started = datetime(2026, 9, 10, 10, 0)
        finished = started + timedelta(minutes=40)
        self.assertEqual(
            calculate_next_run(started, finished, interval_minutes=30),
            datetime(2026, 9, 10, 10, 45),
        )

    def test_rejects_interval_below_desktop_minimum(self):
        now = datetime(2026, 9, 10, 10, 0)
        with self.assertRaisesRegex(ValueError, "30"):
            calculate_next_run(now, now, interval_minutes=29)

    def test_desktop_and_cli_share_the_same_exclusive_process_lock(self):
        with TemporaryDirectory() as temp:
            lock_path = Path(temp) / "airfare-monitor.lock"
            with ProcessLock(lock_path):
                with self.assertRaises(AlreadyRunningError):
                    ProcessLock(lock_path).acquire()

            with ProcessLock(lock_path):
                pass

    def test_run_now_is_coalesced_and_pause_resume_do_not_overlap(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg("route-1")])
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            created: list[_FakeService] = []
            events: list[object] = []

            def factory(legs, settings, sink, delay):
                service = _FakeService(legs, sink, started, release)
                created.append(service)
                return service

            coordinator = MonitorCoordinator(paths, service_factory=factory, jitter=lambda low, high: 0)

            def receive(event: object) -> None:
                events.append(event)
                if isinstance(event, CycleFinished):
                    finished.set()

            coordinator.subscribe(receive)
            coordinator.start()
            self.assertTrue(started.wait(2), "fake monitoring cycle did not start")
            self.assertFalse(coordinator.run_now())
            self.assertFalse(coordinator.run_now())
            self.assertTrue(coordinator.pause())
            release.set()
            self.assertTrue(finished.wait(2), "fake monitoring cycle did not finish")
            self.assertTrue(_wait_until(lambda: coordinator.snapshot().paused))
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0].closed)
            self.assertTrue(coordinator.resume())
            self.assertTrue(_wait_until(lambda: not coordinator.snapshot().paused))
            self.assertEqual(len(created), 1)
            self.assertTrue(any(isinstance(event, NextRunScheduled) for event in events))
            self.assertTrue(coordinator.shutdown(timeout=2))

    def test_start_can_wait_for_onboarding_without_running_a_route(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            RouteRepository(paths.routes_path).save([_leg("route-1")])
            created: list[object] = []

            def factory(legs, settings, sink, delay):
                created.append(object())
                raise AssertionError("service must not be created before onboarding is complete")

            coordinator = MonitorCoordinator(paths, service_factory=factory)
            coordinator.start(run_immediately=False)
            self.assertTrue(_wait_until(lambda: coordinator.snapshot().state == "IDLE"))
            self.assertEqual(created, [])
            self.assertTrue(coordinator.shutdown(timeout=2))

    def test_repository_pauses_only_enabled_routes_before_today(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            yesterday = replace(_leg("expired"), departure_date=date(2026, 9, 20))
            today = replace(_leg("today"), departure_date=date(2026, 9, 21))
            future = replace(_leg("future"), departure_date=date(2026, 9, 22))
            already_paused = replace(yesterday, id="already-paused", enabled=False)
            repository = RouteRepository(paths.routes_path)
            repository.save([yesterday, today, future, already_paused])

            updated, expired = repository.pause_expired(date(2026, 9, 21))

            self.assertEqual([leg.id for leg in expired], ["expired"])
            enabled_by_id = {leg.id: leg.enabled for leg in updated}
            self.assertFalse(enabled_by_id["expired"])
            self.assertFalse(enabled_by_id["already-paused"])
            self.assertTrue(enabled_by_id["today"])
            self.assertTrue(enabled_by_id["future"])
            self.assertEqual(repository.pause_expired(date(2026, 9, 21))[1], ())

    def test_cycle_skips_expired_route_but_keeps_today_route(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = RouteRepository(paths.routes_path)
            repository.save([
                replace(_leg("expired"), departure_date=date(2026, 9, 20)),
                replace(_leg("today"), departure_date=date(2026, 9, 21)),
            ])
            captured_ids: list[str] = []
            events: list[object] = []

            def factory(legs, settings, sink, delay):
                captured_ids.extend(leg.id for leg in legs)
                return _CompletedService(legs, datetime(2026, 9, 21, 10))

            coordinator = MonitorCoordinator(
                paths,
                service_factory=factory,
                now=lambda: datetime(2026, 9, 21, 10),
                jitter=lambda low, high: 0,
            )
            coordinator.subscribe(events.append)

            # Desktop startup reconciles dates even before a browser is ready;
            # the first scheduled cycle must still remain idle afterwards.
            coordinator.reconcile_expired_routes()
            coordinator._execute_cycle(None)

            self.assertEqual(captured_ids, ["today"])
            self.assertFalse(next(leg for leg in repository.load() if leg.id == "expired").enabled)
            expiry = next(event for event in events if isinstance(event, RoutesExpired))
            self.assertEqual([leg.id for leg in expiry.legs], ["expired"])
            self.assertEqual(expiry.remaining_enabled, 1)
            self.assertIsNotNone(coordinator._next_schedule())

    def test_all_expired_routes_stop_without_creating_service(self):
        with TemporaryDirectory() as temp:
            paths = _paths(temp)
            paths.initialize()
            repository = RouteRepository(paths.routes_path)
            repository.save([
                replace(_leg("expired"), departure_date=date(2026, 9, 20)),
            ])
            created: list[object] = []
            events: list[object] = []

            def factory(legs, settings, sink, delay):
                created.append(object())
                raise AssertionError("expired routes must not create a browser service")

            coordinator = MonitorCoordinator(
                paths,
                service_factory=factory,
                now=lambda: datetime(2026, 9, 21, 0, 1),
            )
            coordinator.subscribe(events.append)

            coordinator.reconcile_expired_routes()
            coordinator._execute_cycle(None)

            self.assertEqual(created, [])
            self.assertEqual(coordinator.snapshot().state, "IDLE")
            state_event = next(
                event
                for event in reversed(events)
                if isinstance(event, CoordinatorStateChanged)
            )
            self.assertIn("已过期", state_event.message)
            self.assertIsNone(coordinator._next_schedule())
            self.assertEqual(sum(isinstance(event, RoutesExpired) for event in events), 1)


class _FakeService:
    def __init__(self, legs, sink, started: threading.Event, release: threading.Event):
        self.legs = legs
        self.sink = sink
        self.started = started
        self.release = release
        self.closed = False

    def run_once(self, *, send_email: bool = False):
        started_at = datetime(2026, 9, 10, 10, 0)
        run_id = "fake-run"
        self.sink.on_cycle_started(run_id, started_at, len(self.legs))
        results = []
        for index, leg in enumerate(self.legs, start=1):
            self.sink.on_leg_started(leg, index, len(self.legs))
            self.started.set()
            self.release.wait(2)
            result = LegResult(leg=leg, status=LegStatus.SUCCESS, captured_at=started_at, completed_response=True)
            results.append(result)
            self.sink.on_leg_finished(result, index, len(self.legs))
        report = RunReport(
            run_id=run_id,
            started_at=started_at,
            finished_at=started_at + timedelta(minutes=1),
            status=RunStatus.SUCCESS,
            legs=results,
        )
        self.sink.on_cycle_finished(report, Path("fake.xlsx"))
        return report, Path("fake.xlsx")

    def close(self) -> None:
        self.closed = True


class _CompletedService:
    def __init__(self, legs, finished_at: datetime):
        self.legs = list(legs)
        self.finished_at = finished_at

    def run_once(self, *, send_email: bool = False):
        results = [
            LegResult(
                leg=leg,
                status=LegStatus.SUCCESS,
                captured_at=self.finished_at,
                completed_response=True,
            )
            for leg in self.legs
        ]
        report = RunReport(
            run_id="completed-run",
            started_at=self.finished_at,
            finished_at=self.finished_at,
            status=RunStatus.SUCCESS,
            legs=results,
        )
        return report, Path("completed.xlsx")

    def close(self) -> None:
        pass


def _wait_until(predicate, timeout: float = 2) -> bool:
    event = threading.Event()
    deadline = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < deadline:
        if predicate():
            return True
        event.wait(0.01)
    return predicate()


def _paths(root: str) -> AppPaths:
    discovered = AppPaths.discover(user_root=root)
    fields = {name: getattr(discovered, name) for name in discovered.__dataclass_fields__}
    fields["resource_root"] = ROOT / "resources"
    return AppPaths(**fields)


def _leg(identifier: str) -> LegConfig:
    return LegConfig(
        id=identifier,
        enabled=True,
        origin_airport_iata="SHA",
        destination_airport_iata="XMN",
        departure_date=date(2026, 10, 1),
        etd_window=EtdWindow(time(0, 0), time(23, 59)),
        direct_only=True,
        expected_total_price_cny=None,
        top_n=10,
        adult_count=1,
        child_count=0,
        cabin_class="economy",
        origin_name_zh="上海",
        destination_name_zh="厦门",
    )
