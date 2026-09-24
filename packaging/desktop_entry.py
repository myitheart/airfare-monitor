"""打包入口：同一份冻结运行时按调用方式分流 GUI 与 CLI。

- Finder/open 启动（无参数、或 --background/--ui-smoke-test 等桌面参数）→ 桌面客户端；
- 以 ``airfare-monitor`` 之类的符号链接调用（argv[0] 名不同）或首个参数是
  ``cli``/``validate``/``run-once``/``daemon``/CLI 专属旗标 → 终端 CLI。

两种形态共享同一份数据根（平台用户目录），并由进程锁互斥。
"""

from __future__ import annotations

import os
import sys

_GUI_PROGRAM = "AirfareMonitor"
# 帮助旗标只认首参（避免吞掉 GUI 诊断调用的 --user-root X -h 等组合）；
# 真子命令按“任意位置命中”分流，使 `--user-root X validate` 这类
# 旗标在前的合法顺序也进入 CLI（试用验收 F1）。
_CLI_HELP_FLAGS = {"-h", "--help"}
_CLI_COMMANDS = {"cli", "validate", "run-once", "daemon", "open", "price", "history", "routes", "status", "doctor"}
_CLI_FLAGS = ("--routes", "--settings", "--log-level")


def _wants_cli(argv: list[str]) -> bool:
    program = os.path.basename(argv[0]) if argv else ""
    if program and program != _GUI_PROGRAM:
        return True
    if len(argv) > 1:
        if argv[1] in _CLI_HELP_FLAGS or argv[1].startswith(_CLI_FLAGS):
            return True
    return any(arg in _CLI_COMMANDS for arg in argv[1:])


def main() -> int:
    argv = sys.argv
    if _wants_cli(argv):
        from airfare_monitor.cli import main as cli_main

        rest = argv[2:] if len(argv) > 1 and argv[1] == "cli" else argv[1:]
        return cli_main(rest)
    from airfare_monitor.desktop import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
