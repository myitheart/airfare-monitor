"""Command-driven single-worker scheduler around the existing monitoring core."""

from __future__ import annotations

import queue
import random
import threading
from dataclasses import replace
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from ..app_paths import AppPaths
from ..config import AppSettings, load_routes, load_settings
from ..models import LegConfig, LegResult, RunReport
from ..scheduler import AlreadyRunningError, ProcessLock
from ..service import MonitorEventSink, MonitorService
from ..collector import (
    QunarBrowserSession, build_search_url, build_tongcheng_search_url,
    build_roundtrip_search_url,
)
from ..market import resolve_market
from ..mail import send_report_with_credentials
from .credential_store import CredentialStore, CredentialStoreError
from .mail_profile import MailProfileRepository
from .route_repository import RouteRepository, is_route_expired
from .events import (
    CoordinatorSnapshot,
    CoordinatorStateChanged,
    CycleFinished,
    CycleStarted,
    FatalError,
    LegFinished,
    LegStarted,
    ManualAttentionRequested,
    MailDeliveryFailed,
    VerificationBrowserOpened,
    MonitorCommand,
    MonitorCommandType,
    NextRunScheduled,
    RoutesExpired,
)


class _Service(Protocol):
    def run_once(self, *, send_email: bool = False) -> tuple[RunReport, object]: ...

    def close(self) -> None: ...


ServiceFactory = Callable[
    [list[LegConfig], AppSettings, MonitorEventSink, Callable[[float], None]],
    _Service,
]


class _ShutdownRequested(RuntimeError):
    pass


def calculate_next_run(
    started_at: datetime,
    finished_at: datetime,
    *,
    interval_minutes: int,
    jitter_seconds: float = 0,
) -> datetime:
    """Schedule from cycle start, while always keeping a five-minute cooldown."""
    if interval_minutes < 30:
        raise ValueError("桌面监控间隔不得少于 30 分钟")
    if jitter_seconds < 0:
        raise ValueError("调度抖动秒数不能为负数")
    base_due = started_at + timedelta(minutes=interval_minutes)
    cooldown_due = finished_at + timedelta(minutes=5)
    return max(base_due, cooldown_due) + timedelta(seconds=jitter_seconds)


class MonitorCoordinator:
    """Own one command queue and execute at most one collection cycle at a time."""

    def __init__(
        self,
        paths: AppPaths,
        *,
        service_factory: ServiceFactory | None = None,
        now: Callable[[], datetime] = datetime.now,
        jitter: Callable[[float, float], float] = random.uniform,
    ):
        self.paths = paths
        self._service_factory = service_factory or _default_service_factory
        self._now = now
        self._jitter = jitter
        self._listeners: list[Callable[[object], None]] = []
        self._commands: queue.Queue[MonitorCommand] = queue.Queue()
        self._shutdown = threading.Event()
        self._pause_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = "IDLE"
        self._running = False
        self._run_request_pending = False
        self._next_run_at: datetime | None = None
        self._verification_session: QunarBrowserSession | None = None

    def subscribe(self, listener: Callable[[object], None]) -> None:
        self._listeners.append(listener)

    def reconcile_expired_routes(
        self, checked_at: datetime | None = None,
    ) -> tuple[list[LegConfig], tuple[LegConfig, ...]]:
        """Pause routes whose departure date has passed and announce the change."""
        observed_at = checked_at or self._now()
        configured, expired = RouteRepository(self.paths.routes_path).pause_expired(
            observed_at.date()
        )
        if expired:
            self._emit(RoutesExpired(
                expired,
                observed_at,
                sum(leg.enabled for leg in configured),
            ))
        return configured, expired

    def snapshot(self) -> CoordinatorSnapshot:
        with self._lock:
            return CoordinatorSnapshot(
                state=self._state,
                paused=self._pause_requested.is_set(),
                running=self._running,
                next_run_at=self._next_run_at,
            )

    def start(self, *, run_immediately: bool = True) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._run, name="airfare-monitor-worker", daemon=True)
        self._thread.start()
        if run_immediately:
            self.run_now()

    def run_now(self) -> bool:
        with self._lock:
            if self._running or self._run_request_pending or self._verification_session is not None:
                accepted = False
                message = "已有查询正在运行或等待执行，本次请求已合并"
            elif self._pause_requested.is_set():
                accepted = False
                message = "监控已暂停，请先继续监控"
            else:
                self._run_request_pending = True
                accepted = True
                message = "已加入立即查询队列"
        if accepted:
            self._commands.put(MonitorCommand(MonitorCommandType.RUN_NOW))
        else:
            self._emit(CoordinatorStateChanged(self.snapshot().state, message))
        return accepted

    def retry_leg(self, leg_id: str) -> bool:
        identifier = leg_id.strip()
        if not identifier:
            raise ValueError("重试航程 id 不能为空")
        with self._lock:
            if self._running or self._run_request_pending or self._pause_requested.is_set():
                accepted = False
            else:
                self._run_request_pending = True
                accepted = True
        if accepted:
            self._commands.put(MonitorCommand(MonitorCommandType.RETRY_LEG, identifier))
        else:
            self._emit(CoordinatorStateChanged(self.snapshot().state, "当前无法重试，请等待本轮结束并继续监控"))
        return accepted

    def open_verification(self, leg_id: str) -> bool:
        identifier = leg_id.strip()
        if not identifier:
            return False
        with self._lock:
            if self._running or self._run_request_pending or self._verification_session is not None:
                return False
        self._commands.put(MonitorCommand(MonitorCommandType.OPEN_VERIFICATION, identifier))
        return True

    def pause(self) -> bool:
        if self._pause_requested.is_set():
            return False
        self._pause_requested.set()
        self._commands.put(MonitorCommand(MonitorCommandType.PAUSE))
        message = "当前轮次结束后暂停监控" if self.snapshot().running else "监控已暂停"
        self._set_state("PAUSED", message)
        return True

    def resume(self) -> bool:
        if not self._pause_requested.is_set():
            return False
        self._pause_requested.clear()
        self._commands.put(MonitorCommand(MonitorCommandType.RESUME))
        return True

    def apply_settings(self) -> None:
        self._commands.put(MonitorCommand(MonitorCommandType.APPLY_SETTINGS))

    def shutdown(self, *, timeout: float = 15) -> bool:
        self._shutdown.set()
        self._set_state("EXITING", "正在停止监控并关闭浏览器")
        self._commands.put(MonitorCommand(MonitorCommandType.SHUTDOWN))
        if self._thread:
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def _run(self) -> None:
        try:
            with ProcessLock(self.paths.lock_path):
                self._command_loop()
        except AlreadyRunningError as exc:
            self._set_state("ERROR", "已有另一个监控进程正在运行")
            self._emit(FatalError(type(exc).__name__, str(exc)))
        except Exception as exc:
            self._set_state("ERROR", "监控后台线程异常退出")
            self._emit(FatalError(type(exc).__name__, str(exc)))

    def _command_loop(self) -> None:
        while not self._shutdown.is_set():
            timeout = self._seconds_until_wakeup()
            try:
                command = self._commands.get(timeout=timeout)
            except queue.Empty:
                if not self._pause_requested.is_set() and self._schedule_is_due():
                    self._execute_cycle(None)
                continue

            if command.kind == MonitorCommandType.SHUTDOWN:
                break
            if command.kind == MonitorCommandType.OPEN_VERIFICATION:
                self._open_verification_browser(command.leg_id)
                continue
            if command.kind == MonitorCommandType.PAUSE:
                continue
            if command.kind == MonitorCommandType.RESUME:
                if self._verification_session is not None:
                    self._set_state("ATTENTION", "人工确认页面仍打开；完成后请重试受影响航程")
                    continue
                if self._next_schedule() is None or self._schedule_is_due():
                    self._execute_cycle(None)
                else:
                    self._set_state("IDLE", "监控已继续")
                continue
            if command.kind == MonitorCommandType.APPLY_SETTINGS:
                state = "PAUSED" if self._pause_requested.is_set() else "IDLE"
                self._set_state(state, "设置已更新，将从下一轮查询开始生效")
                continue
            if command.kind in {MonitorCommandType.RUN_NOW, MonitorCommandType.RETRY_LEG}:
                with self._lock:
                    self._run_request_pending = False
                if self._pause_requested.is_set():
                    self._set_state("PAUSED", "监控已暂停")
                    continue
                if self._verification_session is not None:
                    self._close_verification_browser()
                self._execute_cycle(command.leg_id)
        self._close_verification_browser()

    def _open_verification_browser(self, leg_id: str | None) -> None:
        if not leg_id or self.snapshot().running or self._verification_session is not None:
            return
        session: QunarBrowserSession | None = None
        try:
            legs = load_routes(self.paths.routes_path, allow_empty=True)
            leg = next((item for item in legs if item.id == leg_id), None)
            if leg is None:
                raise ValueError("受影响航程已不存在")
            settings = load_settings(self.paths.settings_path, project_root=self.paths.user_root)
            # 人工确认浏览器与采集浏览器共用隔离 Profile（在同一 Profile 上完成
            # 验证码才能真正解除自动化侧的拦截），但使用相邻端口：共用端口时，
            # 用户正在解验证码的可见窗口会与下一轮无头采集产生 headless 状态
            # 冲突，触发 DrissionPage 的浏览器强杀/关闭路径。
            session = QunarBrowserSession(
                replace(settings.browser, headless=False, local_port=settings.browser.local_port + 1)
            )
            session.start()
            assert session.tab is not None
            if resolve_market(leg) == "domestic":
                url = build_tongcheng_search_url(settings.browser.tongcheng_search_url_template, leg)
            elif leg.is_round_trip:
                url = build_roundtrip_search_url(settings.browser.roundtrip_search_url_template, leg)
            else:
                url = build_search_url(settings.browser.search_url_template, leg)
            try:
                session.tab.get(url, timeout=settings.browser.page_load_timeout_seconds)
            except Exception:
                # A challenge page can interrupt navigation. Keep the isolated
                # browser visible so the user can complete the page manually.
                pass
            self._verification_session = session
            self._emit(VerificationBrowserOpened(leg_id))
            self._set_state("ATTENTION", "已打开独立的可见浏览器；请人工完成页面提示")
        except Exception as exc:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            self._emit(FatalError(type(exc).__name__, "无法打开人工确认页面，请检查浏览器后重试"))

    def _close_verification_browser(self) -> None:
        session = self._verification_session
        self._verification_session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def _execute_cycle(self, leg_id: str | None) -> None:
        service: _Service | None = None
        with self._lock:
            if self._running:
                return
            self._running = True
        started_at = self._now()
        try:
            configured, expired = self.reconcile_expired_routes(started_at)
            enabled = [leg for leg in configured if leg.enabled]
            if leg_id is not None:
                enabled = [leg for leg in enabled if leg.id == leg_id]
                if not enabled:
                    if any(leg.id == leg_id for leg in expired):
                        with self._lock:
                            self._next_run_at = None
                        self._set_state("IDLE", "航程出发日期已过，监控已自动暂停")
                        return
                    raise ValueError("需要重试的航程不存在或已暂停")
            if not enabled:
                with self._lock:
                    self._next_run_at = None
                configured_expired = [
                    leg for leg in configured if is_route_expired(leg, started_at.date())
                ]
                if expired:
                    message = f"已自动暂停 {len(expired)} 条过期航程；请编辑日期后重新启用"
                elif configured_expired:
                    message = f"{len(configured_expired)} 条航程已过期；请编辑日期后重新启用"
                else:
                    message = "请先添加并启用至少一条航程"
                self._set_state("IDLE", message)
                return

            settings = load_settings(self.paths.settings_path, project_root=self.paths.user_root)
            mail_profile = MailProfileRepository(
                self.paths.settings_path, user_root=self.paths.user_root
            ).load()
            desktop_mail = None
            if mail_profile.enabled:
                try:
                    secret = CredentialStore().get_secret(mail_profile.username)
                    desktop_mail = (
                        mail_profile.mail_settings(settings.mail),
                        mail_profile.credentials(secret or ""),
                    )
                except (CredentialStoreError, ValueError):
                    self._emit(MailDeliveryFailed("CredentialUnavailable"))
            self._set_state("RUNNING", f"正在串行查询 {len(enabled)} 条航程")
            service = self._service_factory(
                enabled,
                settings,
                _CoordinatorSink(self),
                self._interruptible_delay,
            )
            if desktop_mail is not None and isinstance(service, MonitorService):
                mail_settings, credentials = desktop_mail
                service.mail_delivery = lambda report, workbook: send_report_with_credentials(
                    report, mail_settings, credentials, workbook
                )
            report, workbook = service.run_once(
                send_email=settings.mail.enabled or desktop_mail is not None
            )
            self._emit(CycleFinished(report, str(workbook), len(enabled)))

            if any(result.status.value == "manual_attention" for result in report.legs):
                self._set_state("ATTENTION", "部分航程需要人工处理")
            else:
                self._set_state("IDLE", "本轮查询完成")

            due_at = calculate_next_run(
                started_at,
                report.finished_at,
                interval_minutes=settings.schedule.interval_minutes,
                jitter_seconds=self._jitter(0, settings.schedule.jitter_seconds),
            )
            with self._lock:
                self._next_run_at = due_at
            self._emit(NextRunScheduled(due_at))
        except _ShutdownRequested:
            self._set_state("EXITING", "监控已停止")
        except Exception as exc:
            with self._lock:
                self._next_run_at = None
            self._set_state("ERROR", "监控未能启动；请查看系统状态")
            self._emit(FatalError(type(exc).__name__, str(exc)))
        finally:
            if service is not None:
                try:
                    service.close()
                except Exception as exc:
                    self._emit(FatalError(type(exc).__name__, "浏览器未能正常关闭，请退出应用后重试"))
            with self._lock:
                self._running = False
            if self._pause_requested.is_set():
                self._set_state("PAUSED", "监控已暂停")

    def _interruptible_delay(self, seconds: float) -> None:
        if self._shutdown.wait(max(0, seconds)):
            raise _ShutdownRequested

    def _seconds_until_wakeup(self) -> float | None:
        if self._pause_requested.is_set() or self._verification_session is not None:
            return None
        with self._lock:
            due_at = self._next_run_at
        if due_at is None:
            return None
        remaining = max(0.0, (due_at - self._now()).total_seconds())
        return min(remaining, 30.0)

    def _schedule_is_due(self) -> bool:
        due_at = self._next_schedule()
        return due_at is not None and self._now() >= due_at

    def _next_schedule(self) -> datetime | None:
        with self._lock:
            return self._next_run_at

    def _set_state(self, state: str, message: str) -> None:
        with self._lock:
            self._state = state
        self._emit(CoordinatorStateChanged(state, message))

    def _emit(self, event: object) -> None:
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                continue


def _default_service_factory(
    legs: list[LegConfig],
    settings: AppSettings,
    event_sink: MonitorEventSink,
    delay: Callable[[float], None],
) -> _Service:
    should_continue = getattr(event_sink, "should_continue", lambda: True)
    return MonitorService(
        legs,
        settings,
        event_sink=event_sink,
        sleep=delay,
        should_continue=should_continue,
    )


class _CoordinatorSink(MonitorEventSink):
    def __init__(self, coordinator: MonitorCoordinator):
        self.coordinator = coordinator
        self.run_id = "pending"

    def on_cycle_started(self, run_id: str, started_at: datetime, total: int) -> None:
        self.run_id = run_id
        self.coordinator._emit(CycleStarted(run_id, started_at, total))

    def on_leg_started(self, leg: LegConfig, index: int, total: int) -> None:
        self.coordinator._emit(LegStarted(self.run_id, leg, index, total))

    def on_leg_finished(self, result: LegResult, index: int, total: int) -> None:
        self.coordinator._emit(LegFinished(self.run_id, result, index, total, result.minimum_total_cny))
        if result.status.value == "manual_attention":
            self.coordinator._emit(
                ManualAttentionRequested(result.leg.id, result.error_message or "需要人工处理")
            )

    def on_cycle_finished(self, report: RunReport, workbook: object) -> None:
        return

    def on_mail_failed(self, category: str) -> None:
        self.coordinator._emit(MailDeliveryFailed(category))

    def should_continue(self) -> bool:
        return not self.coordinator._pause_requested.is_set() and not self.coordinator._shutdown.is_set()
