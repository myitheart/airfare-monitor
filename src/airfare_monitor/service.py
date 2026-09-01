"""One complete serial collection/reporting cycle."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable

from .collector import QunarBrowserSession
from .config import AppSettings
from .errors import CollectionError, ManualAttentionRequired
from .excel_report import generate_workbook
from .mail import send_report
from .models import LegConfig, LegResult, LegStatus, PreferredPriceReference, RunReport, RunStatus
from .storage import SQLiteStore

logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(
        self,
        legs: list[LegConfig],
        settings: AppSettings,
        *,
        store: SQLiteStore | None = None,
        browser: QunarBrowserSession | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.legs = [leg for leg in legs if leg.enabled]
        self.settings = settings
        self.store = store or SQLiteStore(settings.storage.sqlite_path)
        self.browser = browser or QunarBrowserSession(settings.browser)
        self.sleep = sleep
        self.now = now
        self.consecutive_failures = 0

    def close(self) -> None:
        self.browser.close()

    def _failed_result(self, leg: LegConfig, exc: Exception) -> LegResult:
        status = LegStatus.MANUAL_ATTENTION if isinstance(exc, ManualAttentionRequired) else LegStatus.FAILED
        return LegResult(
            leg=leg,
            status=status,
            captured_at=self.now(),
            completed_response=False,
            error_category=type(exc).__name__,
            error_message=str(exc),
        )

    def _register_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.settings.browser.restart_after_consecutive_failures:
            logger.warning("连续采集失败 %d 次，重启独立浏览器", self.consecutive_failures)
            self.browser.restart()
            self.consecutive_failures = 0

    def _collect_with_retry(self, leg: LegConfig) -> LegResult:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                result = self.browser.collect(leg, now=self.now)
                self.consecutive_failures = 0
                return result
            except ManualAttentionRequired as exc:
                logger.warning("%s 需要人工处理：%s", leg.id, exc)
                self._register_failure()
                return self._failed_result(leg, exc)
            except CollectionError as exc:
                last_error = exc
                logger.warning("%s 第 %d 次采集失败：%s", leg.id, attempt, exc)
                self._register_failure()
        assert last_error is not None
        return self._failed_result(leg, last_error)

    @staticmethod
    def _status(results: list[LegResult]) -> RunStatus:
        succeeded = sum(result.status == LegStatus.SUCCESS for result in results)
        if succeeded == len(results):
            return RunStatus.SUCCESS
        if succeeded:
            return RunStatus.PARTIAL
        return RunStatus.FAILED

    def _confirm_thresholds(self, results: list[LegResult]) -> set[str]:
        candidates = [result for result in results if result.threshold_hit]
        if not candidates:
            return set()
        delay = self.settings.collection.secondary_confirmation_delay_seconds
        if delay:
            logger.info("发现 %d 个初步低价，%d 秒后二次确认", len(candidates), delay)
            self.sleep(delay)
        confirmed: set[str] = set()
        for initial in candidates:
            try:
                check = self.browser.collect(initial.leg, now=self.now)
            except CollectionError as exc:
                logger.warning("%s 二次确认失败，不标记确认命中：%s", initial.leg.id, exc)
                continue
            check.previous_min_total_cny = initial.previous_min_total_cny
            results[results.index(initial)] = check
            if check.threshold_hit:
                confirmed.add(initial.leg.id)
        return confirmed

    def _attach_preferred_price_references(self, results: list[LegResult], *, before: datetime) -> None:
        for result in results:
            references: list[PreferredPriceReference] = []
            for index, preferred in enumerate(result.leg.preferred_schedules):
                reference = self.store.preferred_price_reference(
                    result.leg.id,
                    preferred.history_key(result.leg.departure_date),
                    before=before,
                )
                flight = result.preferred_matches[index] if index < len(result.preferred_matches) else None
                if reference.first_total_price_cny is None and flight is not None:
                    reference = PreferredPriceReference(
                        first_total_price_cny=flight.total_price_cny,
                        first_captured_at=result.captured_at,
                        previous_total_price_cny=reference.previous_total_price_cny,
                        previous_captured_at=reference.previous_captured_at,
                    )
                references.append(reference)
            result.preferred_price_references = references

    def run_once(self, *, send_email: bool = False) -> tuple[RunReport, object]:
        if not self.legs:
            raise ValueError("没有启用的航程")
        self.store.initialize()
        started_at = self.now()
        run_id = f"{started_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        results: list[LegResult] = []
        for leg in self.legs:
            previous = self.store.previous_minimum(leg.id, before=started_at)
            result = self._collect_with_retry(leg)
            result.previous_min_total_cny = previous
            results.append(result)

        confirmed_ids = self._confirm_thresholds(results)
        self._attach_preferred_price_references(results, before=started_at)
        report = RunReport(
            run_id=run_id,
            started_at=started_at,
            finished_at=self.now(),
            status=self._status(results),
            legs=results,
            threshold_confirmed_leg_ids=confirmed_ids,
        )
        self.store.save_report(report)
        since = report.finished_at - timedelta(hours=self.settings.excel.history_hours)
        history = self.store.history(since=since)
        workbook = generate_workbook(report, history, self.settings.excel.output_directory)
        self.store.prune_raw_responses(self.settings.storage.keep_raw_response_days, now=report.finished_at)
        if send_email:
            send_report(report, self.settings.mail, workbook)
        return report, workbook
