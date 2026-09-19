"""Non-overlapping interval scheduler; each cycle rebuilds the service from disk."""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable
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
        handle: IO[bytes] | None = None
        try:
            handle = self.path.open("a+b")
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if handle is not None:
                handle.close()
            raise AlreadyRunningError("已有监控进程持有运行锁") from exc
        assert handle is not None
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


def run_forever(cycle_factory: Callable[[], MonitorService], lock_path: str | Path) -> None:
    """按配置间隔循环采集；每轮经 ``cycle_factory`` 重建 service。

    与桌面客户端"下一轮生效"的热更新语义保持一致：daemon 运行期间修改
    routes/settings（增删航程、调整间隔、浏览器与邮件设置），下一轮即按
    新配置执行，无需重启。浏览器随轮次启停，同桌面客户端。
    """
    fallback_wait = 300.0
    with ProcessLock(lock_path):
        logger.info("监控调度已启动（每轮重读配置）")
        while True:
            cycle_started = time.monotonic()
            interval: float | None = None
            jitter = 0.0
            service: MonitorService | None = None
            try:
                service = cycle_factory()
                interval = service.settings.schedule.interval_minutes * 60
                jitter = service.settings.schedule.jitter_seconds
                report, workbook = service.run_once(send_email=True)
                logger.info("运行 %s 完成：%s，Excel=%s", report.run_id, report.status, workbook)
            except Exception:
                logger.exception("本轮监控运行失败")
            finally:
                if service is not None:
                    try:
                        service.close()
                    except Exception:
                        logger.exception("关闭本轮采集资源失败")
            elapsed = time.monotonic() - cycle_started
            wait_base = interval if interval is not None else fallback_wait
            wait_seconds = max(0.0, wait_base - elapsed) + (random.uniform(0, jitter) if jitter else 0)
            time.sleep(wait_seconds)
