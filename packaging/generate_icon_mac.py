"""Build a macOS .icns from the application's code-rendered Qt icon."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from airfare_monitor.ui.app_icon import application_icon


ICONSET_SIZES = (16, 32, 128, 256, 512)


def _png_bytes(size: int, icon) -> bytes:
    data = QBuffer()
    data.open(QIODevice.OpenModeFlag.WriteOnly)
    if not icon.pixmap(size, size).save(data, "PNG"):
        raise RuntimeError(f"无法生成 {size}px 图标")
    payload = bytes(data.data())
    data.close()
    return payload


def generate_icns(destination: Path) -> None:
    app = QApplication.instance() or QApplication([])
    icon = application_icon()
    iconset = destination.parent / f"{destination.stem}.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    try:
        for size in ICONSET_SIZES:
            (iconset / f"icon_{size}x{size}.png").write_bytes(_png_bytes(size, icon))
            (iconset / f"icon_{size}x{size}@2x.png").write_bytes(_png_bytes(size * 2, icon))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(destination)], check=True)
    finally:
        shutil.rmtree(iconset, ignore_errors=True)
        _ = app
    if not destination.is_file():
        raise RuntimeError(f"iconutil 未生成 {destination}")


if __name__ == "__main__":
    generate_icns(Path(__file__).resolve().parents[1] / "resources" / "app.icns")
