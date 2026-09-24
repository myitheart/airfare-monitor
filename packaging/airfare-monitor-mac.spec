# -*- mode: python ; coding: utf-8 -*-
"""macOS 打包定义：航价守望窗口客户端 .app。

构建：.venv/bin/python packaging/build_release_mac.py
（或：.venv/bin/pyinstaller packaging/airfare-monitor-mac.spec --noconfirm）
"""

import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

datas = [
    (str(ROOT / "resources" / "airports.zh.json"), "resources"),
    (str(ROOT / "resources" / "routes.default.yaml"), "resources"),
    (str(ROOT / "resources" / "settings.default.yaml"), "resources"),
    (str(ROOT / "resources" / "styles.qss"), "resources"),
    (str(ROOT / "resources" / "i18n" / "qtbase_zh_CN.qm"), "resources/i18n"),
    (str(ROOT / "resources" / "i18n" / "qt_zh_CN.qm"), "resources/i18n"),
    (str(ROOT / "resources" / "aviation-background.png"), "resources"),
    (str(ROOT / "resources" / "aircraft-mark.png"), "resources"),
    (str(ROOT / "resources" / "app-icon-master.png"), "resources"),
]
private_support = ROOT / "private-assets" / "support"
for filename in ("alipay.png", "wechat.png"):
    source = private_support / filename
    if source.is_file():
        datas.append((str(source), "resources/support"))
datas += collect_data_files("airportsdata")
hiddenimports = collect_submodules("DrissionPage")
hiddenimports += collect_submodules("keyring.backends")

a = Analysis(
    [str(ROOT / "packaging" / "desktop_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[], datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="AirfareMonitor", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AirfareMonitor")
bundle = BUNDLE(
    coll,
    name="AirfareMonitor.app",
    icon=str(ROOT / "resources" / "app.icns"),
    info_plist={
        "CFBundleName": "AirfareMonitor",
        "CFBundleDisplayName": "航价守望",
        "CFBundleShortVersionString": VERSION,
        "CFBundleIdentifier": "com.myitheart.airfare-monitor",
        "CFBundleExecutable": "AirfareMonitor",
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticTermination": False,
    },
)
