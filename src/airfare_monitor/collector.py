"""DrissionPage collector for one route at a time."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import quote

from .config import BrowserSettings
from .errors import CollectionError, IncompleteResponseError, ManualAttentionRequired
from .models import LegConfig, LegResult, LegStatus
from .parser import is_completed_payload, parse_completed_payload

LISTEN_TARGET = "/touch/api/inter/wwwsearch"
_VERIFICATION_MARKERS = ("验证码", "安全验证", "设备验证", "访问过于频繁", "captcha")
_SEARCH_INPUTS_SELECTOR = "css:#J_searchBox .inter-search input.serTxt"
_SUGGESTION_SELECTOR = "css:div.m-suggest ul.m-suggest-bd li"
_SEARCH_BUTTON_SELECTOR = "css:#J_searchBox .inter-search button.m-search-btn"


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


class QunarBrowserSession:
    """Own one persistent isolated Chromium instance."""

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
        options.set_local_port(self.settings.local_port)
        options.set_user_data_path(str(self.settings.user_data_path))
        options.headless(self.settings.headless)
        self.browser = Chromium(addr_or_opts=options)
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
        sample = " ".join(
            str(value or "")
            for value in (getattr(self.tab, "url", ""), getattr(self.tab, "title", ""), getattr(self.tab, "html", ""))
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
        expected = f"({code})"
        while time.monotonic() < deadline and expected not in str(input_element.value or "").upper():
            time.sleep(0.1)
        if expected not in str(input_element.value or "").upper():
            raise CollectionError(f"选择第一条联想后未确认代码 {code}")

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

    def collect(self, leg: LegConfig, now: Callable[[], datetime] = datetime.now) -> LegResult:
        self.start()
        assert self.tab is not None
        captured_at = now()
        url = build_search_url(self.settings.search_url_template, leg)
        try:
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
                    flights, preferred_matches, observed_count, eligible_count = parse_completed_payload(
                        payload, leg, captured_at
                    )
                    return LegResult(
                        leg=leg,
                        status=LegStatus.SUCCESS,
                        captured_at=captured_at,
                        flights=flights,
                        preferred_matches=preferred_matches,
                        completed_response=True,
                        observed_count=observed_count,
                        eligible_count=eligible_count,
                        raw_response=payload,
                    )

            if self._verification_visible():
                raise ManualAttentionRequired("页面出现验证码或设备验证，需要人工处理")
            query_id = None
            if last_payload:
                query_id = last_payload.get("result", {}).get("ctrlInfo", {}).get("queryId")
            suffix = f"，queryId={query_id}" if query_id else ""
            raise IncompleteResponseError(f"等待完整搜索响应超时{suffix}")
        finally:
            try:
                self.tab.listen.stop()
            except Exception:
                pass
