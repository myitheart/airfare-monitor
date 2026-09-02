"""Command-line entry points."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import load_local_env, load_routes, load_settings
from .scheduler import run_forever
from .service import MonitorService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="个人多航程航价监控")
    parser.add_argument("--routes", default="config/routes.yaml")
    parser.add_argument("--settings", default="config/settings.yaml")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="仅校验配置，不启动浏览器")
    once = subparsers.add_parser("run-once", help="采集一次并生成 Excel")
    once.add_argument("--send-mail", action="store_true", help="按配置发送真实邮件")
    subparsers.add_parser("daemon", help="按配置间隔运行并发送邮件")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    project_root = Path.cwd()
    load_local_env(project_root / ".env")
    legs = load_routes(args.routes)
    settings = load_settings(args.settings, project_root=project_root)
    if args.command == "validate":
        enabled = [leg for leg in legs if leg.enabled]
        round_trips = sum(leg.is_round_trip for leg in enabled)
        print(f"配置有效：{len(enabled)} 个启用行程，其中 {round_trips} 组往返")
        return 0

    service = MonitorService(legs, settings)
    if args.command == "run-once":
        try:
            report, workbook = service.run_once(send_email=args.send_mail)
            print(f"运行完成：{report.status}；Excel：{workbook}")
            return 0
        finally:
            service.close()
    run_forever(service, settings.storage.sqlite_path.parent / "airfare-monitor.lock")
    return 0
