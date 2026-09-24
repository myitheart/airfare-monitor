"""Offline, versioned airport directory used by the desktop picker."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import airportsdata


_IATA = re.compile(r"^[A-Z]{3}$")
_DISALLOWED_CITY_CODES = {"BJS", "NYC", "LON", "TYO"}
_SOURCES = {"tongcheng", "qunar"}


class AirportCatalogError(ValueError):
    """Raised when a bundled airport directory is invalid."""


@dataclass(frozen=True, slots=True)
class AirportRecord:
    airport_iata: str
    display_name_zh: str
    city_name_zh: str
    airport_name_zh: str | None
    display_name_en: str | None
    country_code: str
    aliases: tuple[str, ...]
    supported_sources: tuple[str, ...]
    verified_at: str | None
    enabled: bool
    is_city: bool = False
    child_airports: tuple[str, ...] = ()

    @property
    def display_text(self) -> str:
        return f"{self.display_name_zh}  {self.airport_iata}"


class AirportCatalog:
    def __init__(
        self,
        version: int,
        records: tuple[AirportRecord, ...],
        cities: tuple[AirportRecord, ...] = (),
    ):
        self.version = version
        self.records = records
        self.cities = cities
        self._enabled = tuple(record for record in records if record.enabled)
        self._enabled_cities = tuple(record for record in cities if record.enabled)

    @classmethod
    def load(cls, path: str | Path) -> "AirportCatalog":
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AirportCatalogError(f"无法读取机场目录：{source}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("catalog_version"), int):
            raise AirportCatalogError("机场目录缺少正整数 catalog_version")
        version = payload["catalog_version"]
        if version <= 0:
            raise AirportCatalogError("机场目录 catalog_version 必须大于 0")
        raw_records = payload.get("airports")
        if not isinstance(raw_records, list):
            raise AirportCatalogError("机场目录 airports 必须是列表")
        airports = airportsdata.load("IATA")
        seen: set[str] = set()
        records: list[AirportRecord] = []
        for index, item in enumerate(raw_records, start=1):
            if not isinstance(item, dict):
                raise AirportCatalogError(f"机场目录第 {index} 项必须是对象")
            code = str(item.get("airport_iata", "")).upper().strip()
            if not _IATA.fullmatch(code) or code in _DISALLOWED_CITY_CODES:
                raise AirportCatalogError(f"机场目录第 {index} 项 IATA 无效：{code!r}")
            if code in seen:
                raise AirportCatalogError(f"机场目录 IATA 重复：{code}")
            known = airports.get(code)
            if known is None:
                raise AirportCatalogError(f"机场目录 IATA 不在 airportsdata 中：{code}")
            country = str(item.get("country_code", "")).upper().strip()
            if country != known["country"]:
                raise AirportCatalogError(f"{code} 的 country_code 与 airportsdata 不一致")
            display = str(item.get("display_name_zh", "")).strip()
            city = str(item.get("city_name_zh", "")).strip()
            if not display or not city:
                raise AirportCatalogError(f"{code} 缺少中文显示名或城市名")
            aliases = item.get("aliases", [])
            sources = item.get("supported_sources", [])
            if not isinstance(aliases, list) or not all(isinstance(value, str) and value.strip() for value in aliases):
                raise AirportCatalogError(f"{code} aliases 必须是非空字符串列表")
            if not isinstance(sources, list) or not sources or not set(sources) <= _SOURCES:
                raise AirportCatalogError(f"{code} supported_sources 无效")
            records.append(
                AirportRecord(
                    airport_iata=code,
                    display_name_zh=display,
                    city_name_zh=city,
                    airport_name_zh=_optional_text(item.get("airport_name_zh")),
                    display_name_en=_optional_text(item.get("display_name_en")),
                    country_code=country,
                    aliases=tuple(value.strip() for value in aliases),
                    supported_sources=tuple(sources),
                    verified_at=_optional_text(item.get("verified_at")),
                    enabled=bool(item.get("enabled", True)),
                )
            )
            seen.add(code)

        raw_cities = payload.get("cities", [])
        cities: list[AirportRecord] = []
        seen_cities: set[str] = set()
        for index, item in enumerate(raw_cities, start=1):
            if not isinstance(item, dict):
                raise AirportCatalogError(f"城市聚合目录第 {index} 项必须是对象")
            code = str(item.get("city_iata", "")).upper().strip()
            if not _IATA.fullmatch(code):
                raise AirportCatalogError(f"城市聚合目录第 {index} 项 city_iata 无效：{code!r}")
            if code in seen_cities:
                raise AirportCatalogError(f"城市聚合目录 city_iata 重复：{code}")
            child_airports = item.get("child_airports", [])
            if not isinstance(child_airports, list) or not child_airports:
                raise AirportCatalogError(f"城市聚合 {code} 必须包含非空 child_airports 列表")
            for child in child_airports:
                if str(child).upper().strip() not in seen:
                    raise AirportCatalogError(f"城市聚合 {code} 的子机场 {child} 不在已知机场目录中")
            display = str(item.get("display_name_zh", "")).strip()
            city = str(item.get("city_name_zh", "")).strip()
            country = str(item.get("country_code", "")).upper().strip()
            aliases = item.get("aliases", [])
            sources = item.get("supported_sources", ["qunar", "tongcheng"])
            cities.append(
                AirportRecord(
                    airport_iata=code,
                    display_name_zh=display,
                    city_name_zh=city,
                    airport_name_zh=_optional_text(item.get("description_zh")),
                    display_name_en=_optional_text(item.get("display_name_en")),
                    country_code=country,
                    aliases=tuple(value.strip() for value in aliases),
                    supported_sources=tuple(sources),
                    verified_at=None,
                    enabled=bool(item.get("enabled", True)),
                    is_city=True,
                    child_airports=tuple(str(c).upper().strip() for c in child_airports),
                )
            )
            seen_cities.add(code)
        return cls(version, tuple(records), tuple(cities))

    def search(self, query: str, *, limit: int = 20, include_cities: bool = False) -> list[AirportRecord]:
        normalized = _normalize(query)
        if not normalized:
            base = list(self._enabled_cities if include_cities else ()) + list(self._enabled)
            return base[:limit]

        city_results: list[AirportRecord] = []
        if include_cities:
            city_ranked: list[tuple[int, AirportRecord]] = []
            for record in self._enabled_cities:
                candidates = (record.airport_iata, record.display_name_zh, record.city_name_zh, *record.aliases)
                normalized_candidates = tuple(_normalize(value) for value in candidates)
                if normalized == _normalize(record.airport_iata) or normalized == _normalize(record.city_name_zh):
                    score = 0
                elif any(value.startswith(normalized) for value in normalized_candidates):
                    score = 1
                elif any(normalized in value for value in normalized_candidates):
                    score = 2
                else:
                    continue
                city_ranked.append((score, record))
            city_ranked.sort(key=lambda pair: (pair[0], pair[1].display_name_zh, pair[1].airport_iata))
            city_results = [record for _, record in city_ranked]

        ranked: list[tuple[int, AirportRecord]] = []
        for record in self._enabled:
            candidates = (record.airport_iata, record.display_name_zh, record.city_name_zh, *record.aliases)
            normalized_candidates = tuple(_normalize(value) for value in candidates)
            if normalized == _normalize(record.airport_iata):
                score = 0
            elif any(value.startswith(normalized) for value in normalized_candidates):
                score = 1
            elif any(normalized in value for value in normalized_candidates):
                score = 2
            else:
                continue
            ranked.append((score, record))
        ranked.sort(key=lambda pair: (pair[0], pair[1].display_name_zh, pair[1].airport_iata))
        airport_results = [record for _, record in ranked]
        return (city_results + airport_results)[:limit]

    def by_iata(self, airport_iata: str, *, allow_city: bool = False) -> AirportRecord | None:
        normalized = airport_iata.upper().strip()
        record = next((r for r in self.records if r.airport_iata == normalized), None)
        if record or not allow_city:
            return record
        return next((c for c in self.cities if c.airport_iata == normalized), None)

    def by_city(self, city_iata: str) -> AirportRecord | None:
        normalized = city_iata.upper().strip()
        return next((c for c in self.cities if c.airport_iata == normalized), None)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
