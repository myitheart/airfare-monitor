"""Stable install-resource and per-user runtime locations for the desktop app."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "AirfareMonitor"


def _default_user_root() -> Path:
    """Per-user runtime root following each platform's convention."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / APP_NAME
    xdg_data = os.environ.get("XDG_DATA_HOME")
    return Path(xdg_data) / APP_NAME if xdg_data else Path.home() / ".local" / "share" / APP_NAME


def _development_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class AppPaths:
    """All application paths, independent of the process working directory."""

    install_root: Path
    resource_root: Path
    user_root: Path
    config_dir: Path
    data_dir: Path
    browser_profile: Path
    logs_dir: Path
    outputs_dir: Path
    routes_path: Path
    settings_path: Path
    database_path: Path
    lock_path: Path

    @classmethod
    def discover(cls, *, user_root: str | Path | None = None) -> "AppPaths":
        if getattr(sys, "frozen", False):
            install_root = Path(sys.executable).resolve().parent
            resource_candidates = (
                Path(getattr(sys, "_MEIPASS", install_root)) / "resources",
                install_root / "_internal" / "resources",
                install_root / "resources",
            )
            resource_root = next((candidate for candidate in resource_candidates if candidate.is_dir()), resource_candidates[0])
        else:
            install_root = _development_root()
            resource_root = install_root / "resources"

        resolved_user_root = Path(user_root) if user_root is not None else _default_user_root()
        config_dir = resolved_user_root / "config"
        data_dir = resolved_user_root / "data"
        return cls(
            install_root=install_root,
            resource_root=resource_root,
            user_root=resolved_user_root,
            config_dir=config_dir,
            data_dir=data_dir,
            browser_profile=data_dir / "browser-profile",
            logs_dir=resolved_user_root / "logs",
            outputs_dir=resolved_user_root / "outputs",
            routes_path=config_dir / "routes.yaml",
            settings_path=config_dir / "settings.yaml",
            database_path=data_dir / "airfare-monitor.sqlite3",
            lock_path=data_dir / "airfare-monitor.lock",
        )

    def initialize(self) -> None:
        for directory in (
            self.config_dir,
            self.data_dir,
            self.browser_profile,
            self.logs_dir,
            self.outputs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._copy_default("routes.default.yaml", self.routes_path)
        self._copy_default("settings.default.yaml", self.settings_path)

    def _copy_default(self, filename: str, destination: Path) -> None:
        if destination.exists():
            return
        source = self.resource_root / filename
        if not source.is_file():
            raise FileNotFoundError(f"缺少应用默认资源：{source}")
        shutil.copyfile(source, destination)
