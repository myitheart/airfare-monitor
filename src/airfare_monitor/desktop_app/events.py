"""Immutable events passed from the monitoring worker to the desktop layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..models import LegConfig, LegResult, RunReport


class MonitorCommandType(StrEnum):
    RUN_NOW = "run_now"
    PAUSE = "pause"
    RESUME = "resume"
    RETRY_LEG = "retry_leg"
    OPEN_VERIFICATION = "open_verification"
    APPLY_SETTINGS = "apply_settings"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class MonitorCommand:
    kind: MonitorCommandType
    leg_id: str | None = None


@dataclass(frozen=True, slots=True)
class CoordinatorSnapshot:
    state: str
    paused: bool
    running: bool
    next_run_at: datetime | None


@dataclass(frozen=True, slots=True)
class CoordinatorStateChanged:
    state: str
    message: str


@dataclass(frozen=True, slots=True)
class CycleStarted:
    run_id: str
    started_at: datetime
    total_legs: int


@dataclass(frozen=True, slots=True)
class LegStarted:
    run_id: str
    leg: LegConfig
    index: int
    total: int


@dataclass(frozen=True, slots=True)
class LegFinished:
    run_id: str
    result: LegResult
    index: int
    total: int
    minimum_total_cny: Decimal | None


@dataclass(frozen=True, slots=True)
class CycleFinished:
    report: RunReport
    workbook_path: str
    total_legs: int | None = None


@dataclass(frozen=True, slots=True)
class NextRunScheduled:
    due_at: datetime


@dataclass(frozen=True, slots=True)
class RoutesExpired:
    legs: tuple[LegConfig, ...]
    checked_at: datetime
    remaining_enabled: int


@dataclass(frozen=True, slots=True)
class ManualAttentionRequested:
    leg_id: str
    message: str


@dataclass(frozen=True, slots=True)
class FatalError:
    category: str
    user_message: str


@dataclass(frozen=True, slots=True)
class MailDeliveryFailed:
    category: str


@dataclass(frozen=True, slots=True)
class VerificationBrowserOpened:
    leg_id: str
