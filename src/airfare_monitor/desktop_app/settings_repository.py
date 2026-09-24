"""Desktop-only preferences layered into the existing settings YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import AppSettings, load_settings
from .yaml_files import atomic_write_yaml


@dataclass(frozen=True, slots=True)
class DesktopSettings:
    browser_kind: str = "auto"
    browser_path: str | None = None
    interval_minutes: int = 30
    show_browser: bool = True
    desktop_notifications: bool = True
    autostart: bool = False
    launch_to_tray: bool = False
    onboarding_completed: bool = False
    close_to_tray_confirmed: bool = False


class SettingsRepository:
    def __init__(self, path: str | Path, *, user_root: str | Path):
        self.path = Path(path)
        self.user_root = Path(user_root)

    def load_core(self) -> AppSettings:
        return load_settings(self.path, project_root=self.user_root)

    def load_desktop(self) -> DesktopSettings:
        raw = self._raw()
        desktop = raw.get("desktop", {})
        schedule = raw.get("schedule", {})
        browser = raw.get("browser", {})
        if not isinstance(desktop, dict):
            raise ValueError("settings.desktop 必须是映射")
        if not isinstance(schedule, dict) or not isinstance(browser, dict):
            raise ValueError("settings.schedule 和 settings.browser 必须是映射")
        kind = str(desktop.get("browser_kind", "auto")).lower()
        if kind not in {"auto", "chrome", "edge"}:
            raise ValueError("browser_kind 必须是 auto、chrome 或 edge")
        browser_path = desktop.get("browser_path", browser.get("executable_path"))
        if browser_path is not None and not isinstance(browser_path, str):
            raise ValueError("browser_path 必须是字符串或 null")
        interval_minutes = schedule.get("interval_minutes", 30)
        if interval_minutes not in {30, 60, 120}:
            raise ValueError("桌面查询间隔必须是 30、60 或 120 分钟")
        return DesktopSettings(
            browser_kind=kind,
            browser_path=browser_path or None,
            interval_minutes=interval_minutes,
            show_browser=not _boolean(browser.get("headless", False), "browser.headless"),
            desktop_notifications=_boolean(desktop.get("desktop_notifications", True), "desktop_notifications"),
            autostart=_boolean(desktop.get("autostart", False), "autostart"),
            launch_to_tray=_boolean(desktop.get("launch_to_tray", False), "launch_to_tray"),
            onboarding_completed=_boolean(
                desktop.get("onboarding_completed", False), "onboarding_completed"
            ),
            close_to_tray_confirmed=_boolean(
                desktop.get("close_to_tray_confirmed", False), "close_to_tray_confirmed"
            ),
        )

    def save_desktop(self, value: DesktopSettings) -> None:
        if value.browser_kind not in {"auto", "chrome", "edge"}:
            raise ValueError("browser_kind 必须是 auto、chrome 或 edge")
        if value.interval_minutes not in {30, 60, 120}:
            raise ValueError("桌面查询间隔必须是 30、60 或 120 分钟")
        raw = self._raw()
        schedule = raw.get("schedule")
        browser = raw.get("browser")
        if not isinstance(schedule, dict) or not isinstance(browser, dict):
            raise ValueError("settings.schedule 和 settings.browser 必须是映射")
        schedule["interval_minutes"] = value.interval_minutes
        browser["headless"] = not value.show_browser
        browser["executable_path"] = value.browser_path
        raw["desktop"] = {
            "browser_kind": value.browser_kind,
            "browser_path": value.browser_path,
            "desktop_notifications": value.desktop_notifications,
            "autostart": value.autostart,
            "launch_to_tray": value.launch_to_tray,
            "onboarding_completed": value.onboarding_completed,
            "close_to_tray_confirmed": value.close_to_tray_confirmed,
        }
        atomic_write_yaml(
            self.path,
            raw,
            validate=lambda temporary: load_settings(temporary, project_root=self.user_root),
        )

    def _raw(self) -> dict[str, Any]:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"无法读取设置：{self.path}") from exc
        if not isinstance(raw, dict):
            raise ValueError("settings.yaml 必须是映射")
        return raw


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return value
