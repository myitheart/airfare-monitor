"""DrissionPage collector for one route at a time."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote

from .config import BrowserSettings, MAX_STORED_CANDIDATES
from .errors import CollectionError, IncompleteResponseError, ManualAttentionRequired
from .market import resolve_market
from .models import LegConfig, LegResult, LegStatus
from .parser import is_completed_payload, parse_completed_payload
from .tongcheng_parser import is_completed_tongcheng_page_state, parse_tongcheng_page_state

logger = logging.getLogger(__name__)

LISTEN_TARGET = "/touch/api/inter/wwwsearch"
_VERIFICATION_MARKERS = ("验证码", "安全验证", "设备验证", "访问过于频繁", "captcha")
_SEARCH_INPUTS_SELECTOR = "css:#J_searchBox .inter-search input.serTxt"
_SUGGESTION_SELECTOR = "css:div.m-suggest ul.m-suggest-bd li"
_SEARCH_BUTTON_SELECTOR = "css:#J_searchBox .inter-search button.m-search-btn"
_SELECTED_CODE_PATTERN = re.compile(r"\(([A-Z]{3})\)")


def _selected_code(value: Any) -> str | None:
    """Return the IATA-like code the suggestion box actually committed, e.g. 上海(SHA) -> SHA."""
    match = _SELECTED_CODE_PATTERN.search(str(value or "").upper())
    return match.group(1) if match else None


def build_search_url(template: str, leg: LegConfig) -> str:
    values = {
        "origin": quote(leg.origin_airport_iata),
        "destination": quote(leg.destination_airport_iata),
        "date": quote(leg.departure_date.isoformat()),
        "adult_count": leg.adult_count,
        "child_count": leg.child_count,
        "cabin_class": quote(leg.cabin_class),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise CollectionError(f"search_url_template 含未知占位符：{exc.args[0]}") from exc


def build_tongcheng_search_url(template: str, leg: LegConfig) -> str:
    values = {
        "origin": quote(leg.origin_airport_iata),
        "destination": quote(leg.destination_airport_iata),
        "date": quote(leg.departure_date.isoformat()),
        "origin_name": quote(leg.origin_name_zh or leg.origin_airport_iata),
        "destination_name": quote(leg.destination_name_zh or leg.destination_airport_iata),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise CollectionError(f"tongcheng_search_url_template 含未知占位符：{exc.args[0]}") from exc


def build_roundtrip_search_url(template: str, leg: LegConfig) -> str:
    if leg.return_date is None:
        raise CollectionError("往返搜索缺少 return_date")
    values = {
        "origin": quote(leg.origin_airport_iata),
        "destination": quote(leg.destination_airport_iata),
        "origin_name": quote(leg.origin_name_zh or leg.origin_airport_iata),
        "destination_name": quote(leg.destination_name_zh or leg.destination_airport_iata),
        "date": quote(leg.departure_date.isoformat()),
        "return_date": quote(leg.return_date.isoformat()),
        "adult_count": leg.adult_count,
        "child_count": leg.child_count,
        "cabin_class": quote(leg.cabin_class),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise CollectionError(f"roundtrip_search_url_template 含未知占位符：{exc.args[0]}") from exc


def _response_body(packet: Any) -> dict[str, Any] | None:
    if packet is False or packet is None:
        return None
    body = getattr(getattr(packet, "response", None), "body", None)
    if isinstance(body, dict):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


_GRACEFUL_CHROMIUM_CACHE: dict[type, type] = {}


def _graceful_chromium_cls(chromium_cls: type) -> type:
    """返回把强杀降级为优雅关闭的 Chromium 子类（按基类缓存）。"""
    cached = _GRACEFUL_CHROMIUM_CACHE.get(chromium_cls)
    if cached is None:

        def quit(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            force = kwargs.get("force") if "force" in kwargs else (args[1] if len(args) >= 2 else False)
            if force:
                logger.warning("拦截浏览器强杀请求，改为优雅关闭（headless 状态不一致的复用场景）")
                if "force" in kwargs:
                    kwargs["force"] = False
                elif len(args) >= 2:
                    args = args[:1] + (False,) + args[2:]
            return chromium_cls.quit(self, *args, **kwargs)

        cached = type("GracefulChromium", (chromium_cls,), {"quit": quit})
        _GRACEFUL_CHROMIUM_CACHE[chromium_cls] = cached
    return cached


class QunarBrowserSession:
    """Own one Chromium instance and select Qunar or Tongcheng per route."""

    def __init__(self, settings: BrowserSettings):
        self.settings = settings
        self.browser: Any = None
        self.tab: Any = None

    def start(self) -> None:
        if self.browser is not None:
            return
        try:
            from DrissionPage import Chromium, ChromiumOptions
        except ImportError as exc:
            raise CollectionError("未安装 DrissionPage；请先安装项目依赖") from exc

        self.settings.user_data_path.mkdir(parents=True, exist_ok=True)
        options = ChromiumOptions(read_file=False)
        if self.settings.executable_path is not None:
            options.set_browser_path(str(self.settings.executable_path))
        options.set_local_port(self.settings.local_port)
        options.set_user_data_path(str(self.settings.user_data_path))
        options.headless(self.settings.headless)
        # 复用分支雷：DrissionPage 在「端口上已有浏览器且 headless 状态不一致」时
        # 会 quit(3, True) 走 psutil 强杀 Chrome（macOS App Management 一级触发器）。
        # 子类把任何 force 关闭降级为优雅关闭（CDP Browser.close）。
        self.browser = _graceful_chromium_cls(Chromium)(addr_or_opts=options)
        self.tab = self.browser.latest_tab
        self.tab.set.timeouts(
            base=self.settings.search_completion_timeout_seconds,
            page_load=self.settings.page_load_timeout_seconds,
        )

    def restart(self) -> None:
        self.close()
        self.start()

    def close(self) -> None:
        if self.browser is not None:
            try:
                self.browser.quit()
            finally:
                self.browser = None
                self.tab = None

    def _verification_visible(self) -> bool:
        if self.tab is None:
            return False
        body = self.tab.ele("tag:body", timeout=0.5)
        sample = " ".join(
            str(value or "")
            for value in (
                getattr(self.tab, "url", ""),
                getattr(self.tab, "title", ""),
                getattr(body, "text", "") if body is not None else "",
            )
        ).lower()
        return any(marker.lower() in sample for marker in _VERIFICATION_MARKERS)

    def _visible(self, selector: str) -> list[Any]:
        assert self.tab is not None
        return [element for element in self.tab.eles(selector) if element.states.is_displayed]

    def _choose_first_suggestion(self, input_element: Any, code: str) -> None:
        input_element.click()
        input_element.input(code, clear=True)
        deadline = time.monotonic() + 8
        choices: list[Any] = []
        while time.monotonic() < deadline:
            choices = self._visible(_SUGGESTION_SELECTOR)
            if choices:
                break
            time.sleep(0.1)
        if not choices:
            raise CollectionError(f"{code} 未出现机场/城市联想项")
        choices[0].click()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _selected_code(input_element.value) is None:
            time.sleep(0.1)
        selected = _selected_code(input_element.value)
        if selected is None:
            raise CollectionError(f"选择第一条联想后未确认代码 {code}")
        if selected != code:
            # 去哪儿会把部分机场聚合为城市代码（如 PVG→SHA、NRT→TYO、LHR→LON），
            # 联想框回填的是聚合代码。这不算失败：结果仍按真实机场解析与记录。
            logger.info("%s 在去哪儿联想中被聚合为城市代码 %s，按该城市继续查询", code, selected)

    def _choose_departure_date(self, input_element: Any, leg: LegConfig) -> None:
        input_element.click()
        target_month = f"{leg.departure_date.year}年{leg.departure_date.month}月"
        target_day = str(leg.departure_date.day)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert self.tab is not None
            candidates = [
                element
                for element in self.tab.eles(f'xpath://span[contains(@class,"day") and normalize-space(text())="{target_day}"]')
                if element.states.is_displayed
            ]
            for candidate in candidates:
                month_panel = candidate.parent(7)
                if month_panel and month_panel.text.splitlines()[0].strip() == target_month:
                    day_link = candidate.parent(1)
                    if "disabled" in str(day_link.attr("class") or ""):
                        continue
                    day_link.click()
                    if str(input_element.value or "") != leg.departure_date.isoformat():
                        raise CollectionError(
                            f"日期选择结果不一致：期望 {leg.departure_date.isoformat()}，实际 {input_element.value}"
                        )
                    return
            time.sleep(0.1)
        raise CollectionError(f"日历未显示可选日期 {leg.departure_date.isoformat()}")

    def _submit_search_form(self, leg: LegConfig) -> None:
        inputs = self._visible(_SEARCH_INPUTS_SELECTOR)
        if len(inputs) < 3:
            raise CollectionError("未找到去哪儿国际单程搜索框")
        self._choose_first_suggestion(inputs[0], leg.origin_airport_iata)
        self._choose_first_suggestion(inputs[1], leg.destination_airport_iata)
        self._choose_departure_date(inputs[2], leg)
        buttons = self._visible(_SEARCH_BUTTON_SELECTOR)
        if not buttons:
            raise CollectionError("未找到去哪儿国际机票搜索按钮")
        assert self.tab is not None
        self.tab.listen.start(LISTEN_TARGET)
        buttons[0].click()

    def _collect_qunar(self, leg: LegConfig, captured_at: datetime) -> LegResult:
        assert self.tab is not None
        url = build_search_url(self.settings.search_url_template, leg)
        self.tab.get(url, timeout=self.settings.page_load_timeout_seconds)
        self._submit_search_form(leg)
        deadline = time.monotonic() + self.settings.search_completion_timeout_seconds
        last_payload: dict[str, Any] | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            packet = self.tab.listen.wait(timeout=remaining, raise_err=False)
            payload = _response_body(packet)
            if payload is None:
                break
            last_payload = payload
            if is_completed_payload(payload):
                ranked, preferred_matches, observed_count, eligible_count = parse_completed_payload(
                    payload, _candidate_leg(leg), captured_at
                )
                return LegResult(
                    leg=leg,
                    status=LegStatus.SUCCESS,
                    captured_at=captured_at,
                    flights=ranked[: leg.top_n],
                    candidate_flights=ranked[:MAX_STORED_CANDIDATES],
                    preferred_matches=preferred_matches,
                    completed_response=True,
                    observed_count=observed_count,
                    eligible_count=eligible_count,
                    raw_response=payload,
                )
        if self._verification_visible():
            raise ManualAttentionRequired("页面出现验证码或设备验证，需要人工处理")
        query_id = last_payload.get("result", {}).get("ctrlInfo", {}).get("queryId") if last_payload else None
        suffix = f"，queryId={query_id}" if query_id else ""
        raise IncompleteResponseError(f"等待去哪儿完整搜索响应超时{suffix}")

    def _collect_qunar_round_trip(self, leg: LegConfig, captured_at: datetime) -> LegResult:
        assert self.tab is not None
        url = build_roundtrip_search_url(self.settings.roundtrip_search_url_template, leg)
        self.tab.listen.start(LISTEN_TARGET)
        self.tab.get(url, timeout=self.settings.page_load_timeout_seconds)
        deadline = time.monotonic() + self.settings.search_completion_timeout_seconds
        last_payload: dict[str, Any] | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            packet = self.tab.listen.wait(timeout=remaining, raise_err=False)
            payload = _response_body(packet)
            if payload is None:
                break
            last_payload = payload
            if is_completed_payload(payload):
                ranked, preferred_matches, observed_count, eligible_count = parse_completed_payload(
                    payload, _candidate_leg(leg), captured_at
                )
                return LegResult(
                    leg=leg,
                    status=LegStatus.SUCCESS,
                    captured_at=captured_at,
                    flights=ranked[: leg.top_n],
                    candidate_flights=ranked[:MAX_STORED_CANDIDATES],
                    preferred_matches=preferred_matches,
                    completed_response=True,
                    observed_count=observed_count,
                    eligible_count=eligible_count,
                    raw_response=payload,
                )
        if self._verification_visible():
            raise ManualAttentionRequired("页面出现验证码或设备验证，需要人工处理")
        query_id = last_payload.get("result", {}).get("ctrlInfo", {}).get("queryId") if last_payload else None
        suffix = f"，queryId={query_id}" if query_id else ""
        raise IncompleteResponseError(f"等待去哪儿完整往返搜索响应超时{suffix}")

    def _collect_tongcheng(self, leg: LegConfig, captured_at: datetime) -> LegResult:
        if not leg.direct_only:
            raise CollectionError("同程国内采集目前只支持 direct_only: true，以保证完整航段签名")
        assert self.tab is not None
        url = build_tongcheng_search_url(self.settings.tongcheng_search_url_template, leg)
        self.tab.get(url, timeout=self.settings.page_load_timeout_seconds)
        deadline = time.monotonic() + self.settings.search_completion_timeout_seconds
        last_state: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            state = self.tab.run_js(
                "const b=window.__NUXT__?.state?.book1;"
                "if(!b)return null;"
                "return JSON.parse(JSON.stringify({"
                "flightLists:b.flightLists,dataflag:b.dataflag,Departure:b.Departure,"
                "Arrival:b.Arrival,DepartureDate:b.DepartureDate}));"
            )
            if isinstance(state, dict):
                last_state = state
            if is_completed_tongcheng_page_state(state, leg):
                ranked, preferred_matches, observed_count, eligible_count = parse_tongcheng_page_state(
                    state, _candidate_leg(leg), captured_at
                )
                return LegResult(
                    leg=leg,
                    status=LegStatus.SUCCESS,
                    captured_at=captured_at,
                    flights=ranked[: leg.top_n],
                    candidate_flights=ranked[:MAX_STORED_CANDIDATES],
                    preferred_matches=preferred_matches,
                    completed_response=True,
                    observed_count=observed_count,
                    eligible_count=eligible_count,
                    raw_response={"tongcheng_page_state": state},
                )
            time.sleep(0.2)
        if self._verification_visible():
            raise ManualAttentionRequired("同程页面出现验证码或设备验证，需要人工处理")
        state_summary = ""
        if last_state:
            state_summary = (
                f"，dataflag={last_state.get('dataflag')}，"
                f"route={last_state.get('Departure')}-{last_state.get('Arrival')}，"
                f"date={last_state.get('DepartureDate')}"
            )
        raise IncompleteResponseError(f"等待同程最终页面状态超时{state_summary}")

    def collect(self, leg: LegConfig, now: Callable[[], datetime] = datetime.now) -> LegResult:
        self.start()
        assert self.tab is not None
        captured_at = now()
        try:
            market = resolve_market(leg)
            if leg.is_round_trip:
                if market == "domestic":
                    raise CollectionError("往返合计价采集目前只支持去哪儿跨境/国际航线")
                return self._collect_qunar_round_trip(leg, captured_at)
            if market == "domestic":
                return self._collect_tongcheng(leg, captured_at)
            return self._collect_qunar(leg, captured_at)
        finally:
            try:
                self.tab.listen.stop()
            except Exception:
                pass


def _candidate_leg(leg: LegConfig) -> LegConfig:
    """Ask existing parsers for enough ranked rows without changing route rules."""

    return replace(leg, top_n=max(leg.top_n, MAX_STORED_CANDIDATES))
