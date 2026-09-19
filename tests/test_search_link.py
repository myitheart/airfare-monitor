"""来源网站直达链接（search_link）与 CLI open 子命令的测试。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from airfare_monitor.cli import _open_route
from airfare_monitor.config import load_settings
from airfare_monitor.models import LegConfig
from airfare_monitor.search_link import search_site_label, search_url_for

_EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "settings.example.yaml"


def _leg(**overrides: object) -> LegConfig:
    values: dict[str, object] = {
        "id": "t",
        "enabled": True,
        "origin_name_zh": "上海",
        "destination_name_zh": "东京",
        "origin_airport_iata": "SHA",
        "destination_airport_iata": "TYO",
        "departure_date": date(2026, 10, 2),
        "etd_window": ("00:00", "23:59"),
        "direct_only": True,
        "expected_total_price_cny": None,
        "top_n": 5,
        "adult_count": 2,
        "child_count": 0,
        "cabin_class": "economy",
    }
    values.update(overrides)
    return LegConfig(**values)  # type: ignore[arg-type]


class SearchLinkTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        path = Path(self._temporary.name) / "settings.yaml"
        shutil.copy2(_EXAMPLE, path)
        self.settings = load_settings(path)

    def tearDown(self):
        self._temporary.cleanup()

    def test_international_oneway_uses_qunar(self):
        url = search_url_for(_leg(), self.settings)
        self.assertIn("flight.qunar.com/site/oneway_list_inter.htm", url)
        self.assertIn("fromCode=SHA", url)
        self.assertIn("toCode=TYO", url)
        self.assertIn("adultNum=2", url)
        self.assertEqual(search_site_label(_leg()), "去哪儿")

    def test_domestic_oneway_uses_tongcheng(self):
        leg = _leg(
            origin_name_zh="上海",
            destination_name_zh="北京",
            origin_airport_iata="SHA",
            destination_airport_iata="BJS",
        )
        url = search_url_for(leg, self.settings)
        self.assertIn("www.ly.com/flights/itinerary/oneway/SHA-BJS", url)
        self.assertIn("date=2026-10-02", url)
        self.assertEqual(search_site_label(leg), "同程")

    def test_international_roundtrip_uses_qunar_roundtrip(self):
        leg = _leg(return_date=date(2026, 12, 1))
        url = search_url_for(leg, self.settings)
        self.assertIn("flight.qunar.com/site/interroundtrip_compare.htm", url)
        self.assertIn("fromDate=2026-10-02", url)
        self.assertIn("toDate=2026-12-01", url)


class CliOpenTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        path = Path(self._temporary.name) / "settings.yaml"
        shutil.copy2(_EXAMPLE, path)
        self.settings = load_settings(path)
        self.legs = [_leg(id="sha-tyo"), _leg(id="sha-bjs", destination_airport_iata="BJS", destination_name_zh="北京")]

    def tearDown(self):
        self._temporary.cleanup()

    def test_open_without_id_lists_routes(self):
        with mock.patch("builtins.print") as printer:
            code = _open_route(self.legs, self.settings, None)
        self.assertEqual(code, 0)
        text = "\n".join(str(call.args[0]) for call in printer.call_args_list)
        self.assertIn("sha-tyo", text)
        self.assertIn("去哪儿", text)
        self.assertIn("同程", text)

    def test_open_unknown_id_fails_fast(self):
        with mock.patch("builtins.print"):
            self.assertEqual(_open_route(self.legs, self.settings, "nope"), 1)

    def test_open_known_id_opens_browser(self):
        with mock.patch("builtins.print"):
            with mock.patch("webbrowser.open") as opener:
                code = _open_route(self.legs, self.settings, "sha-tyo")
        self.assertEqual(code, 0)
        (url,), _ = opener.call_args
        self.assertIn("flight.qunar.com/site/oneway_list_inter.htm", url)
        self.assertIn("fromCode=SHA", url)


if __name__ == "__main__":
    unittest.main()
