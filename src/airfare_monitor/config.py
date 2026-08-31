"""YAML configuration loading with strict validation."""

from __future__ import annotations

import re
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .models import EtdWindow, LegConfig

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_CABIN_CLASSES = {"economy", "premium_economy", "business", "first"}


def load_local_env(path: str | Path, *, override: bool = False) -> None:
    """Load a simple, git-ignored KEY=VALUE file without logging values."""
    env_path = Path(path)
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"无法读取本地环境配置：{env_path}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"{env_path} 第 {line_number} 行必须是 KEY=VALUE")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ConfigError(f"{env_path} 第 {line_number} 行变量名无效")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if override or name not in os.environ:
            os.environ[name] = value


@dataclass(frozen=True, slots=True)
class ScheduleSettings:
    timezone: str
    interval_minutes: int
    jitter_seconds: int
    prevent_overlapping_runs: bool


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    headless: bool
    user_data_path: Path
    local_port: int
    page_load_timeout_seconds: int
    search_completion_timeout_seconds: int
    restart_after_consecutive_failures: int
    search_url_template: str


@dataclass(frozen=True, slots=True)
class CollectionSettings:
    source: str
    currency: str
    require_completed_response: bool
    secondary_confirmation_delay_seconds: int


@dataclass(frozen=True, slots=True)
class StorageSettings:
    sqlite_path: Path
    keep_raw_response_days: int


@dataclass(frozen=True, slots=True)
class ExcelSettings:
    output_directory: Path
    history_hours: int


@dataclass(frozen=True, slots=True)
class MailSettings:
    enabled: bool
    smtp_host: str
    smtp_port: int
    security: str
    username_env: str
    password_env: str
    sender_env: str
    recipients_env: str
    attach_excel: bool
    normal_subject_prefix: str
    threshold_subject_prefix: str


@dataclass(frozen=True, slots=True)
class AppSettings:
    schedule: ScheduleSettings
    browser: BrowserSettings
    collection: CollectionSettings
    storage: StorageSettings
    excel: ExcelSettings
    mail: MailSettings


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} 必须是映射")
    return value


def _required(mapping: dict[str, Any], key: str, path: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise ConfigError(f"缺少配置 {path}.{key}")
    return mapping[key]


def _positive_int(value: Any, path: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path} 必须是整数")
    if value < 0 if allow_zero else value <= 0:
        limit = "非负" if allow_zero else "正"
        raise ConfigError(f"{path} 必须是{limit}整数")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{path} 必须是 true 或 false")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} 必须是非空字符串")
    return value.strip()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"无法读取配置 {path}: {exc}") from exc
    return _mapping(content, str(path))


def _parse_date(value: Any, path: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(_string(value, path))
    except ValueError as exc:
        raise ConfigError(f"{path} 必须是 YYYY-MM-DD") from exc


def _parse_time(value: Any, path: str) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = _string(value, path)
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
    except ValueError as exc:
        raise ConfigError(f"{path} 必须是 HH:MM，例如 08:30") from exc
    return parsed


def _parse_decimal(value: Any, path: str) -> Decimal:
    if isinstance(value, bool):
        raise ConfigError(f"{path} 必须是正数")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{path} 必须是数字") from exc
    if parsed <= 0:
        raise ConfigError(f"{path} 必须大于 0")
    return parsed


def load_routes(path: str | Path) -> list[LegConfig]:
    root = _load_yaml(Path(path))
    raw_legs = _required(root, "legs", "routes")
    if not isinstance(raw_legs, list) or not raw_legs:
        raise ConfigError("routes.legs 必须是非空列表")

    legs: list[LegConfig] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_legs, start=1):
        item = _mapping(raw, f"routes.legs[{index}]")
        prefix = f"routes.legs[{index}]"
        leg_id = _string(_required(item, "id", prefix), f"{prefix}.id")
        if leg_id in seen_ids:
            raise ConfigError(f"航程 id 重复：{leg_id}")
        seen_ids.add(leg_id)

        origin = _string(_required(item, "origin_airport_iata", prefix), f"{prefix}.origin_airport_iata").upper()
        destination = _string(_required(item, "destination_airport_iata", prefix), f"{prefix}.destination_airport_iata").upper()
        if not _IATA_RE.fullmatch(origin) or not _IATA_RE.fullmatch(destination):
            raise ConfigError(f"{prefix} 的机场 IATA 必须是三个英文字母")
        if origin == destination:
            raise ConfigError(f"{prefix} 的出发和到达机场不能相同")

        window = _mapping(_required(item, "etd_window", prefix), f"{prefix}.etd_window")
        cabin = _string(_required(item, "cabin_class", prefix), f"{prefix}.cabin_class").lower()
        if cabin not in _CABIN_CLASSES:
            raise ConfigError(f"{prefix}.cabin_class 不受支持：{cabin}")

        legs.append(
            LegConfig(
                id=leg_id,
                enabled=_boolean(_required(item, "enabled", prefix), f"{prefix}.enabled"),
                origin_airport_iata=origin,
                destination_airport_iata=destination,
                departure_date=_parse_date(_required(item, "departure_date", prefix), f"{prefix}.departure_date"),
                etd_window=EtdWindow(
                    start=_parse_time(_required(window, "start", f"{prefix}.etd_window"), f"{prefix}.etd_window.start"),
                    end=_parse_time(_required(window, "end", f"{prefix}.etd_window"), f"{prefix}.etd_window.end"),
                ),
                direct_only=_boolean(_required(item, "direct_only", prefix), f"{prefix}.direct_only"),
                expected_total_price_cny=_parse_decimal(
                    _required(item, "expected_total_price_cny", prefix), f"{prefix}.expected_total_price_cny"
                ),
                top_n=_positive_int(_required(item, "top_n", prefix), f"{prefix}.top_n"),
                adult_count=_positive_int(_required(item, "adult_count", prefix), f"{prefix}.adult_count"),
                child_count=_positive_int(
                    _required(item, "child_count", prefix), f"{prefix}.child_count", allow_zero=True
                ),
                cabin_class=cabin,
                origin_name_zh=(
                    _string(item["origin_name_zh"], f"{prefix}.origin_name_zh")
                    if item.get("origin_name_zh") is not None
                    else None
                ),
                destination_name_zh=(
                    _string(item["destination_name_zh"], f"{prefix}.destination_name_zh")
                    if item.get("destination_name_zh") is not None
                    else None
                ),
            )
        )
    if not any(leg.enabled for leg in legs):
        raise ConfigError("至少需要启用一个航程")
    return legs


def load_settings(path: str | Path, *, project_root: str | Path | None = None) -> AppSettings:
    config_path = Path(path)
    root_dir = Path(project_root) if project_root else config_path.resolve().parent.parent
    raw = _load_yaml(config_path)
    schedule = _mapping(_required(raw, "schedule", "settings"), "settings.schedule")
    browser = _mapping(_required(raw, "browser", "settings"), "settings.browser")
    collection = _mapping(_required(raw, "collection", "settings"), "settings.collection")
    storage = _mapping(_required(raw, "storage", "settings"), "settings.storage")
    excel = _mapping(_required(raw, "excel", "settings"), "settings.excel")
    mail = _mapping(_required(raw, "mail", "settings"), "settings.mail")

    security = _string(_required(mail, "security", "settings.mail"), "settings.mail.security").lower()
    if security not in {"ssl", "starttls"}:
        raise ConfigError("settings.mail.security 必须是 ssl 或 starttls")

    def relative_path(value: Any, setting_path: str) -> Path:
        candidate = Path(_string(value, setting_path))
        return candidate if candidate.is_absolute() else root_dir / candidate

    return AppSettings(
        schedule=ScheduleSettings(
            timezone=_string(_required(schedule, "timezone", "settings.schedule"), "settings.schedule.timezone"),
            interval_minutes=_positive_int(
                _required(schedule, "interval_minutes", "settings.schedule"), "settings.schedule.interval_minutes"
            ),
            jitter_seconds=_positive_int(
                _required(schedule, "jitter_seconds", "settings.schedule"),
                "settings.schedule.jitter_seconds",
                allow_zero=True,
            ),
            prevent_overlapping_runs=_boolean(
                _required(schedule, "prevent_overlapping_runs", "settings.schedule"),
                "settings.schedule.prevent_overlapping_runs",
            ),
        ),
        browser=BrowserSettings(
            headless=_boolean(_required(browser, "headless", "settings.browser"), "settings.browser.headless"),
            user_data_path=relative_path(
                _required(browser, "user_data_path", "settings.browser"), "settings.browser.user_data_path"
            ),
            local_port=_positive_int(browser.get("local_port", 9333), "settings.browser.local_port"),
            page_load_timeout_seconds=_positive_int(
                _required(browser, "page_load_timeout_seconds", "settings.browser"),
                "settings.browser.page_load_timeout_seconds",
            ),
            search_completion_timeout_seconds=_positive_int(
                _required(browser, "search_completion_timeout_seconds", "settings.browser"),
                "settings.browser.search_completion_timeout_seconds",
            ),
            restart_after_consecutive_failures=_positive_int(
                _required(browser, "restart_after_consecutive_failures", "settings.browser"),
                "settings.browser.restart_after_consecutive_failures",
            ),
            search_url_template=_string(
                _required(browser, "search_url_template", "settings.browser"), "settings.browser.search_url_template"
            ),
        ),
        collection=CollectionSettings(
            source=_string(_required(collection, "source", "settings.collection"), "settings.collection.source"),
            currency=_string(
                _required(collection, "currency", "settings.collection"), "settings.collection.currency"
            ).upper(),
            require_completed_response=_boolean(
                _required(collection, "require_completed_response", "settings.collection"),
                "settings.collection.require_completed_response",
            ),
            secondary_confirmation_delay_seconds=_positive_int(
                _required(collection, "secondary_confirmation_delay_seconds", "settings.collection"),
                "settings.collection.secondary_confirmation_delay_seconds",
                allow_zero=True,
            ),
        ),
        storage=StorageSettings(
            sqlite_path=relative_path(
                _required(storage, "sqlite_path", "settings.storage"), "settings.storage.sqlite_path"
            ),
            keep_raw_response_days=_positive_int(
                _required(storage, "keep_raw_response_days", "settings.storage"),
                "settings.storage.keep_raw_response_days",
                allow_zero=True,
            ),
        ),
        excel=ExcelSettings(
            output_directory=relative_path(
                _required(excel, "output_directory", "settings.excel"), "settings.excel.output_directory"
            ),
            history_hours=_positive_int(
                _required(excel, "history_hours", "settings.excel"), "settings.excel.history_hours"
            ),
        ),
        mail=MailSettings(
            enabled=_boolean(_required(mail, "enabled", "settings.mail"), "settings.mail.enabled"),
            smtp_host=_string(_required(mail, "smtp_host", "settings.mail"), "settings.mail.smtp_host"),
            smtp_port=_positive_int(_required(mail, "smtp_port", "settings.mail"), "settings.mail.smtp_port"),
            security=security,
            username_env=_string(_required(mail, "username_env", "settings.mail"), "settings.mail.username_env"),
            password_env=_string(_required(mail, "password_env", "settings.mail"), "settings.mail.password_env"),
            sender_env=_string(_required(mail, "sender_env", "settings.mail"), "settings.mail.sender_env"),
            recipients_env=_string(
                _required(mail, "recipients_env", "settings.mail"), "settings.mail.recipients_env"
            ),
            attach_excel=_boolean(_required(mail, "attach_excel", "settings.mail"), "settings.mail.attach_excel"),
            normal_subject_prefix=_string(
                _required(mail, "normal_subject_prefix", "settings.mail"), "settings.mail.normal_subject_prefix"
            ),
            threshold_subject_prefix=_string(
                _required(mail, "threshold_subject_prefix", "settings.mail"),
                "settings.mail.threshold_subject_prefix",
            ),
        ),
    )
