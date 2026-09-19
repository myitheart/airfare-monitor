"""Qt 标准控件文案中文化：让 QMessageBox 的 OK/Yes 等按钮跟随中文界面。"""

from __future__ import annotations

from pathlib import Path

_TRANSLATORS: list[object] = []
_QM_NAMES = ("qtbase_zh_CN.qm", "qt_zh_CN.qm")


def install_chinese_translations(app, resource_root: Path) -> None:
    """加载随应用分发的 qtbase_zh_CN 翻译；重复调用是安全的。

    打包后翻译位于 resource_root/i18n（与 styles.qss 同级分发），
    开发环境缺文件时回退到 PySide6 自带翻译目录。
    """
    if _TRANSLATORS:
        return
    from PySide6.QtCore import QLibraryInfo, QTranslator

    candidates = [Path(resource_root) / "i18n", Path(QLibraryInfo.path(QLibraryInfo.TranslationsPath))]
    for base in candidates:
        pending: list[QTranslator] = []
        for name in _QM_NAMES:
            translator = QTranslator(app)
            if translator.load(str(base / name)):
                app.installTranslator(translator)
                pending.append(translator)
        if pending:
            _TRANSLATORS.extend(pending)
            return
