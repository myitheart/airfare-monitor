"""Non-overlapping interval scheduler with a persistent browser session."""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from types import TracebackType
from typing import IO

from .service import MonitorService

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    pass


class ProcessLock:
    """Hold a non-blocking OS file lock for the scheduler lifetime."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.handle: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise AlreadyRunningError("已有监控进程持有运行锁") from exc
        self.handle = handle

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def run_forever(service: MonitorService, lock_path: str | Path) -> None:
    interval = service.settings.schedule.interval_minutes * 60
    jitter = service.settings.schedule.jitter_seconds
    with ProcessLock(lock_path):
        logger.info("监控调度已启动，间隔 %d 分钟", service.settings.schedule.interval_minutes)
        try:
            while True:
                cycle_started = time.monotonic()
                try:
                    report, workbook = service.run_once(send_email=True)
                    logger.info("运行 %s 完成：%s，Excel=%s", report.run_id, report.status, workbook)
                except Exception:
                    logger.exception("本轮监控运行失败")
                elapsed = time.monotonic() - cycle_started
                wait_seconds = max(0.0, interval - elapsed) + (random.uniform(0, jitter) if jitter else 0)
                time.sleep(wait_seconds)
        finally:
            service.close()
