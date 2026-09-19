"""Locate installed Chrome or Edge without attaching to a daily browser profile."""

from __future__ import annotations

import os
import plistlib
import sys
from dataclasses import dataclass
from pathlib import Path


def _is_mac() -> bool:
    return sys.platform == "darwin"


@dataclass(frozen=True, slots=True)
class BrowserCandidate:
    kind: str
    path: Path
    version: str | None


class BrowserDetector:
    _REGISTRY_NAMES = {
        "chrome": "chrome.exe",
        "edge": "msedge.exe",
    }

    def detect(self, *, preferred_path: str | Path | None = None) -> list[BrowserCandidate]:
        found: list[BrowserCandidate] = []
        seen: set[Path] = set()
        candidates: list[tuple[str, Path]] = []
        if preferred_path:
            preferred = Path(preferred_path)
            candidates.append((self._kind_for_path(preferred), preferred))
        for kind in ("chrome", "edge"):
            candidates.extend((kind, path) for path in self._registry_paths(kind))
            candidates.extend((kind, path) for path in self._common_paths(kind))
        for kind, path in candidates:
            resolved = path.expanduser()
            if not resolved.is_file() or resolved in seen:
                continue
            found.append(BrowserCandidate(kind=kind, path=resolved, version=self._version_for(kind, resolved)))
            seen.add(resolved)
        return found

    @staticmethod
    def select(
        candidates: list[BrowserCandidate],
        *,
        preferred_kind: str = "auto",
        preferred_path: str | Path | None = None,
    ) -> BrowserCandidate | None:
        if preferred_path:
            expected = Path(preferred_path)
            for candidate in candidates:
                try:
                    if candidate.path.samefile(expected):
                        return candidate
                except OSError:
                    if candidate.path == expected:
                        return candidate
            return None
        if preferred_kind in {"chrome", "edge"}:
            return next((candidate for candidate in candidates if candidate.kind == preferred_kind), None)
        return candidates[0] if candidates else None

    @staticmethod
    def _kind_for_path(path: Path) -> str:
        return "edge" if "edge" in path.name.lower() else "chrome"

    def _version_for(self, kind: str, path: Path) -> str | None:
        if _is_mac():
            return self._bundle_version(path)
        return self._registry_version(kind)

    @staticmethod
    def _bundle_version(path: Path) -> str | None:
        """Read CFBundleShortVersionString from a macOS .app bundle."""
        for parent in path.parents:
            if parent.suffix == ".app" and (parent / "Contents" / "Info.plist").is_file():
                try:
                    with (parent / "Contents" / "Info.plist").open("rb") as stream:
                        info = plistlib.load(stream)
                    version = info.get("CFBundleShortVersionString")
                    return str(version) if version else None
                except (OSError, plistlib.InvalidFileException):
                    return None
        return None

    def _registry_paths(self, kind: str) -> list[Path]:
        if os.name != "nt":
            return []
        try:
            import winreg
        except ImportError:
            return []
        paths: list[Path] = []
        executable = self._REGISTRY_NAMES[kind]
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}") as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    paths.append(Path(value))
            except OSError:
                continue
        return paths

    def _registry_version(self, kind: str) -> str | None:
        if os.name != "nt":
            return None
        try:
            import winreg
        except ImportError:
            return None
        product_key = "Google\\Chrome\\BLBeacon" if kind == "chrome" else "Microsoft\\Edge\\BLBeacon"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\{product_key}") as key:
                    value, _ = winreg.QueryValueEx(key, "version")
                    return str(value)
            except OSError:
                continue
        return None

    @staticmethod
    def _common_paths(kind: str) -> list[Path]:
        if _is_mac():
            bundles = ("Google Chrome", "Chromium") if kind == "chrome" else ("Microsoft Edge",)
            roots = (Path("/Applications"), Path.home() / "Applications")
            return [
                root / f"{bundle}.app" / "Contents" / "MacOS" / bundle
                for root in roots
                for bundle in bundles
            ]
        if os.name != "nt":
            names = ("google-chrome", "google-chrome-stable", "chromium") if kind == "chrome" else ("microsoft-edge",)
            return [Path("/usr/bin") / name for name in names] + [Path("/usr/local/bin") / name for name in names]
        executable = "chrome.exe" if kind == "chrome" else "msedge.exe"
        product = "Google\\Chrome" if kind == "chrome" else "Microsoft\\Edge"
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        return [Path(root) / product / "Application" / executable for root in roots if root]
