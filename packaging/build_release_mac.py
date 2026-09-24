"""Repeatable, ad-hoc signed macOS release builder (.app + DMG, no notarization)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def release_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    package = ROOT / "src" / "airfare_monitor" / "__init__.py"
    if f'__version__ = "{version}"' not in package.read_text(encoding="utf-8"):
        raise RuntimeError("Python 包版本号与 pyproject.toml 不一致")
    return version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_app(version: str) -> Path:
    icon = ROOT / "resources" / "app.icns"
    if not icon.is_file():
        raise FileNotFoundError("缺少 resources/app.icns；先运行 packaging/generate_icon_mac.py")
    build_root = ROOT / "build" / f"release-mac-{version}"
    dist_root = ROOT / "dist" / f"release-mac-{version}"
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--workpath", str(build_root), "--distpath", str(dist_root),
         str(ROOT / "packaging" / "airfare-monitor-mac.spec")],
        cwd=ROOT, check=True,
    )
    app = dist_root / "AirfareMonitor.app"
    if not (app / "Contents" / "MacOS" / "AirfareMonitor").is_file():
        raise RuntimeError("PyInstaller 未生成 AirfareMonitor.app")
    return app


def smoke_test(app: Path) -> None:
    binary = app / "Contents" / "MacOS" / "AirfareMonitor"
    with TemporaryDirectory(prefix="airfare-mac-smoke-") as temporary:
        smoke_root = Path(temporary)
        report = smoke_root / "result.json"
        subprocess.run(
            [str(binary), "--ui-smoke-test",
             "--user-root", str(smoke_root / "user"), "--smoke-report", str(report)],
            cwd=ROOT, check=True, timeout=120,
        )
        payload = json.loads(report.read_text(encoding="utf-8"))
        if payload.get("status") != "ok":
            raise RuntimeError(f"打包后的 Qt UI 烟测失败：{payload}")


DEV_SIGNING_IDENTITY = "AirfareMonitor Local Dev"


def _signing_identity() -> str:
    """优先使用本机自签名开发证书（稳定身份 → TCC 隐私授权跨构建保留）。

    证书由 packaging/make_signing_identity.sh 一次性生成并导入登录钥匙串；
    缺失时回退 ad-hoc（每次构建身份不同，隐私授权会反复重弹）。
    """
    result = subprocess.run(
        ["security", "find-certificate", "-c", DEV_SIGNING_IDENTITY],
        capture_output=True, text=True,
    )
    return DEV_SIGNING_IDENTITY if result.returncode == 0 else "-"


def sign_app(app: Path) -> None:
    identity = _signing_identity()
    subprocess.run(["codesign", "--force", "--deep", "--sign", identity, str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    requirement = subprocess.run(
        ["codesign", "--display", "-r-", str(app)], capture_output=True, text=True,
    )
    print(f"签名身份：{identity}")
    for line in requirement.stdout.splitlines():
        if line.startswith("designated"):
            print(line.strip())


def include_agent_instructions(app: Path) -> None:
    """把 AI 安装说明放进 bundle 的标准位置，供 AI 助手按 AGENTS.md 惯例发现。"""
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "packaging" / "AGENTS.md", resources / "AGENTS.md")


def build_dmg(app: Path, version: str, output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    dmg = output_root / f"AirfareMonitor-{version}-macOS.dmg"
    with TemporaryDirectory(prefix="airfare-dmg-") as temporary:
        staging = Path(temporary) / "AirfareMonitor"
        staging.mkdir()
        shutil.copytree(app, staging / "AirfareMonitor.app", symlinks=True)
        shutil.copy2(ROOT / "packaging" / "AGENTS.md", staging / "AGENTS.md")
        (staging / "Applications").symlink_to("/Applications")
        subprocess.run(
            ["hdiutil", "create", "-volname", "AirfareMonitor",
             "-srcfolder", str(staging), "-ov", "-format", "ULFO", str(dmg)],
            check=True,
        )
    if not dmg.is_file():
        raise RuntimeError("hdiutil 未生成 DMG")
    return dmg


def build_release() -> Path:
    version = release_version()
    app = build_app(version)
    smoke_test(app)
    include_agent_instructions(app)
    sign_app(app)
    output_root = ROOT / "release" / f"v{version}-mac"
    installer = build_dmg(app, version, output_root)
    checksum = output_root / f"{installer.name}.sha256"
    checksum.write_text(f"{sha256_file(installer)}  {installer.name}\n", encoding="ascii")
    print(f"DMG：{installer}\nSHA-256：{checksum}\n应用：{app}")
    return installer


if __name__ == "__main__":
    build_release()
