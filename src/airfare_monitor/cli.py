"""Command-line entry points."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from .app_paths import AppPaths
from .config import AppSettings, load_local_env, load_routes, load_settings
from .errors import ConfigError
from .models import LegConfig
from .scheduler import run_forever
from .service import MonitorService

logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="个人多航程航价监控")
    parser.add_argument("--user-root", help="数据根目录；缺省与桌面客户端共用同一平台用户目录")
    parser.add_argument("--routes", help="航程配置；缺省 <数据根>/config/routes.yaml")
    parser.add_argument("--settings", help="运行配置；缺省 <数据根>/config/settings.yaml")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="仅校验配置，不启动浏览器")
    once = subparsers.add_parser("run-once", help="采集一次并生成 Excel")
    once.add_argument("--send-mail", action="store_true", help="按配置发送真实邮件")
    subparsers.add_parser("daemon", help="按配置间隔运行并发送邮件")
    opener = subparsers.add_parser("open", help="在默认浏览器打开航程对应来源网站的搜索结果页")
    opener.add_argument("route_id", nargs="?", help="航程 ID；缺省列出全部可选航程")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        return _dispatch(args)
    except ConfigError as exc:
        # 配置类错误打印单行中文提示，不抛裸 traceback（试用验收 F2）。
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    paths = AppPaths.discover(user_root=args.user_root)
    load_local_env(Path.cwd() / ".env")
    routes_path = Path(args.routes) if args.routes else paths.routes_path
    settings_path = Path(args.settings) if args.settings else paths.settings_path
    # project_root 留空：settings.yaml 里的相对路径（data/、outputs/ 等）
    # 锚定到该配置文件所在的 <数据根>，与桌面客户端的解析口径一致。
    # allow_empty 与桌面客户端对齐：0 条启用时 daemon 允许空转等待配置，
    # run-once 会在执行期给出明确报错（试用验收 F3）。
    legs = load_routes(routes_path, allow_empty=True)
    settings = load_settings(settings_path)
    if args.command == "validate":
        enabled = [leg for leg in legs if leg.enabled]
        round_trips = sum(leg.is_round_trip for leg in enabled)
        print(f"配置有效：{len(enabled)} 个启用行程，其中 {round_trips} 组往返")
        return 0

    if args.command == "run-once":
        if not any(leg.enabled for leg in legs):
            print("没有启用的航程；请先在配置或桌面客户端中启用至少一条。", file=sys.stderr)
            return 2
        service = MonitorService(legs, settings)
        _attach_desktop_mail(service, settings_path)
        try:
            report, workbook = service.run_once(send_email=args.send_mail)
            print(f"运行完成：{report.status}；Excel：{workbook}")
            return 0
        finally:
            service.close()

    if args.command == "open":
        return _open_route(legs, settings, args.route_id)

    lock_path = settings.storage.sqlite_path.parent / "airfare-monitor.lock"
    run_forever(_daemon_cycle_factory(routes_path, settings_path), lock_path)
    return 0


def _open_route(legs: list[LegConfig], settings: AppSettings, route_id: str | None) -> int:
    import webbrowser

    from .search_link import search_site_label, search_url_for

    if route_id is None:
        if not legs:
            print("还没有任何航程；先用桌面客户端或编辑 routes.yaml 添加。")
            return 0
        print("可选航程（airfare-monitor open <ID>）：")
        for leg in legs:
            arrow = "⇄" if leg.is_round_trip else "→"
            state = "启用" if leg.enabled else "暂停"
            print(
                f"  {leg.id:<24} {leg.origin_name_zh or leg.origin_airport_iata} {arrow} "
                f"{leg.destination_name_zh or leg.destination_airport_iata}  "
                f"{leg.departure_date.isoformat()}  {search_site_label(leg)}  {state}"
            )
        return 0
    leg = next((item for item in legs if item.id == route_id), None)
    if leg is None:
        print(f"未找到航程：{route_id}（不带参数运行 airfare-monitor open 查看全部 ID）")
        return 1
    url = search_url_for(leg, settings)
    print(f"{search_site_label(leg)}：{url}")
    webbrowser.open(url)
    return 0


def _daemon_cycle_factory(routes_path: Path, settings_path: Path) -> Callable[[], MonitorService]:
    """每轮重读 routes/settings 构造 service，与桌面客户端热更新语义一致。"""

    def factory() -> MonitorService:
        service = MonitorService(load_routes(routes_path, allow_empty=True), load_settings(settings_path))
        _attach_desktop_mail(service, settings_path)
        return service

    return factory


def _attach_desktop_mail(service: MonitorService, settings_path: Path) -> None:
    """挂接与桌面客户端同源的邮件通道：desktop_mail（钥匙串授权码）优先。

    未启用或读取失败时保持 mail_delivery 为空，run_once 会回退到
    settings.mail 的环境变量通道（enabled=false 时静默跳过）。
    """
    from .desktop_app.credential_store import CredentialStore
    from .desktop_app.mail_profile import MailProfileRepository
    from .mail import send_report_with_credentials

    try:
        profile = MailProfileRepository(settings_path, user_root=settings_path.resolve().parent.parent).load()
    except Exception as exc:
        logger.warning("读取桌面邮件配置失败，本轮回退环境变量邮件通道：%s", exc)
        return
    if not profile.enabled:
        return
    try:
        secret = CredentialStore().get_secret(profile.username)
    except Exception as exc:
        logger.warning("读取邮箱授权码失败，本轮跳过桌面邮件：%s", exc)
        return
    service.mail_delivery = lambda report, workbook: send_report_with_credentials(
        report,
        profile.mail_settings(service.settings.mail),
        profile.credentials(secret or ""),
        workbook,
    )
