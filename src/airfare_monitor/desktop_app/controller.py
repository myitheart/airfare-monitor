"""Application commands shared by desktop pages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date

from ..config import MAX_ENABLED_LEGS
from ..models import LegConfig
from .route_repository import RouteRepository, is_route_expired


class DesktopController:
    def __init__(self, routes: RouteRepository, *, today: Callable[[], date] = date.today):
        self.routes = routes
        self._today = today
        self._listeners: list[Callable[[list[LegConfig]], None]] = []

    def current_routes(self) -> list[LegConfig]:
        return self.routes.load()

    def on_routes_changed(self, listener: Callable[[list[LegConfig]], None]) -> None:
        self._listeners.append(listener)

    def save_route(self, route: LegConfig) -> None:
        if route.enabled and is_route_expired(route, self._today()):
            raise ValueError("出发日期已过，请先修改为今天或未来日期再启用")
        current = self.current_routes()
        replaced = False
        updated: list[LegConfig] = []
        for existing in current:
            if existing.id == route.id:
                updated.append(route)
                replaced = True
            else:
                updated.append(existing)
        if not replaced:
            updated.append(route)
        self.routes.save(updated)
        self._emit(updated)

    def delete_route(self, route_id: str) -> None:
        updated = [route for route in self.current_routes() if route.id != route_id]
        self.routes.save(updated)
        self._emit(updated)

    def toggle_route(self, route_id: str, enabled: bool) -> None:
        current = self.current_routes()
        route = next((item for item in current if item.id == route_id), None)
        if route is None:
            raise ValueError("航程不存在或已被删除")
        if enabled and is_route_expired(route, self._today()):
            raise ValueError("出发日期已过，请先编辑航程日期再启用")
        updated = [route if route.id != route_id else _with_enabled(route, enabled) for route in current]
        self.routes.save(updated)
        self._emit(updated)

    def enabled_capacity_remaining(self, *, excluding_id: str | None = None) -> int:
        used = sum(route.enabled and route.id != excluding_id for route in self.current_routes())
        return max(0, MAX_ENABLED_LEGS - used)

    def _emit(self, routes: list[LegConfig]) -> None:
        for listener in tuple(self._listeners):
            listener(routes)


def _with_enabled(route: LegConfig, enabled: bool) -> LegConfig:
    return replace(route, enabled=enabled)
