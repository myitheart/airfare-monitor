"""非交互航程构造（route_builder）与 routes add/edit/show/remove 的测试。"""

from __future__ import annotations

import shutil
import unittest
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from airfare_monitor.cli import main as cli_main
from airfare_monitor.config import load_routes
from airfare_monitor.desktop_app.airport_catalog import AirportCatalog
from airfare_monitor.route_builder import build_leg, parse_preferred, resolve_endpoint

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "resources" / "airports.zh.json"
_EXAMPLE_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.example.yaml"


def _catalog() -> AirportCatalog:
    return AirportCatalog.load(_CATALOG_PATH)


class ResolveEndpointTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _catalog()

    def test_iata_code_direct(self):
        record = resolve_endpoint(self.catalog, "PVG")
        self.assertEqual(record.airport_iata, "PVG")
        self.assertFalse(record.is_city)

    def test_city_code_resolves_aggregate(self):
        record = resolve_endpoint(self.catalog, "TYO")
        self.assertTrue(record.is_city)
        self.assertIn("NRT", record.child_airports)

    def test_bare_city_name_prefers_aggregate(self):
        record = resolve_endpoint(self.catalog, "上海")
        self.assertTrue(record.is_city)
        self.assertEqual(record.airport_iata, "SHA")

    def test_display_name_exact(self):
        record = resolve_endpoint(self.catalog, "东京成田")
        self.assertEqual(record.airport_iata, "NRT")
        self.assertFalse(record.is_city)

    def test_ambiguous_name_lists_candidates(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_endpoint(self.catalog, "东")
        message = str(ctx.exception)
        self.assertIn("NRT", message)
        self.assertIn("多个匹配", message)


class BuildLegTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _catalog()

    def test_add_city_aggregate_roundtrip(self):
        draft = build_leg(
            self.catalog,
            origin="上海",
            destination="东京",
            departure_date="2026-11-26",
            return_date="2026-12-01",
            etd_start=None,
            etd_end=None,
            direct_only=True,
            threshold="1500",
            adults=2,
            children=0,
            cabin=None,
            preferred=["早班直飞,08:00,12:00,30"],
            route_id=None,
        )
        leg = draft.leg
        self.assertEqual(leg.origin_airport_iata, "SHA")
        self.assertEqual(set(leg.origin_airports or ()), {"PVG", "SHA"})
        self.assertTrue(leg.is_round_trip)
        self.assertIsNotNone(leg.return_etd_window)
        self.assertTrue(leg.return_direct_only)
        self.assertEqual(leg.preferred_schedules[0].origin_airport_iata, None, "城市聚合的重点班次按全城匹配")
        self.assertEqual(leg.adult_count, 2)
        self.assertEqual(str(leg.expected_total_price_cny), "1500")

    def test_domestic_roundtrip_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build_leg(
                self.catalog,
                origin="上海",
                destination="北京",
                departure_date="2026-11-26",
                return_date="2026-12-01",
                etd_start=None,
                etd_end=None,
                direct_only=None,
                threshold=None,
                adults=None,
                children=None,
                cabin=None,
                preferred=None,
                route_id=None,
            )
        self.assertIn("国际", str(ctx.exception))

    def test_edit_preserves_aggregate(self):
        first = build_leg(
            self.catalog,
            origin="上海",
            destination="大阪",
            departure_date="2026-11-26",
            return_date=None,
            etd_start=None,
            etd_end=None,
            direct_only=None,
            threshold=None,
            adults=None,
            children=None,
            cabin=None,
            preferred=None,
            route_id="r1",
        ).leg
        second = build_leg(
            self.catalog,
            origin=None,
            destination=None,
            departure_date=None,
            return_date=None,
            etd_start=None,
            etd_end=None,
            direct_only=None,
            threshold="1800",
            adults=None,
            children=None,
            cabin=None,
            preferred=None,
            route_id=None,
            existing=first,
        ).leg
        self.assertEqual(set(second.origin_airports or ()), {"PVG", "SHA"}, "编辑不得把城市聚合降级为单机场")
        self.assertEqual(second.id, "r1")
        self.assertEqual(str(second.expected_total_price_cny), "1800")

    def test_parse_preferred_formats(self):
        schedule = parse_preferred("早班,08:05,12:10")
        self.assertEqual(schedule.departure_time, time(8, 5))
        self.assertEqual(schedule.departure_tolerance_minutes, 30)
        with self.assertRaises(ValueError):
            parse_preferred("只有一段")


class CliRoutesLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.root = Path(self._temporary.name)
        (self.root / "config").mkdir()
        shutil.copy2(_EXAMPLE_SETTINGS, self.root / "config" / "settings.yaml")
        (self.root / "config" / "routes.yaml").write_text("legs: []\n", encoding="utf-8")

    def tearDown(self):
        self._temporary.cleanup()

    def _run(self, *args: str) -> int:
        with mock.patch("builtins.print"):
            return cli_main(["--user-root", str(self.root), *args])

    def test_add_show_edit_remove_roundtrip(self):
        self.assertEqual(self._run("routes", "add", "--from", "上海", "--to", "东京", "--date", "2026-11-26"), 0)
        legs = load_routes(self.root / "config" / "routes.yaml", allow_empty=True)
        self.assertEqual(len(legs), 1)
        self.assertEqual(set(legs[0].origin_airports or ()), {"PVG", "SHA"})
        self.assertTrue(legs[0].enabled)

        self.assertEqual(self._run("routes", "show", legs[0].id), 0)
        self.assertEqual(self._run("routes", "edit", legs[0].id, "--threshold", "1800"), 0)
        legs = load_routes(self.root / "config" / "routes.yaml", allow_empty=True)
        self.assertEqual(str(legs[0].expected_total_price_cny), "1800")

        self.assertEqual(self._run("routes", "disable", legs[0].id), 0)
        legs = load_routes(self.root / "config" / "routes.yaml", allow_empty=True)
        self.assertFalse(legs[0].enabled)

        self.assertEqual(self._run("routes", "remove", legs[0].id), 0)
        legs = load_routes(self.root / "config" / "routes.yaml", allow_empty=True)
        self.assertEqual(legs, [])

    def test_add_json_output(self):
        with mock.patch("builtins.print") as printer:
            code = cli_main(
                [
                    "--user-root", str(self.root), "--json",
                    "routes", "add", "--from", "PVG", "--to", "NRT", "--date", "2026-11-26",
                ]
            )
        self.assertEqual(code, 0)
        import json

        payload = None
        for call in printer.call_args_list:
            try:
                candidate = json.loads(str(call.args[0]))
                if isinstance(candidate, dict) and "saved" in candidate:
                    payload = candidate
            except json.JSONDecodeError:
                continue
        assert payload is not None
        self.assertEqual(payload["saved"]["origin_airports"], [])
        self.assertEqual(payload["saved"]["destination"], "东京 NRT")

    def test_add_bad_airport_fails_with_candidates(self):
        with mock.patch("builtins.print"):
            code = cli_main(
                ["--user-root", str(self.root), "routes", "add", "--from", "不存在的城市", "--to", "NRT",
                 "--date", "2026-11-26"]
            )
        self.assertEqual(code, 2)

    def test_duplicate_id_rejected(self):
        self.assertEqual(self._run("routes", "add", "--from", "PVG", "--to", "NRT", "--date", "2026-11-26"), 0)
        self.assertEqual(
            self._run("routes", "add", "--id", "pvg-nrt-20261126", "--from", "PVG", "--to", "NRT", "--date", "2026-11-26"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
