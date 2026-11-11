from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from .models import YouyunApiError, youyun_zone_supports_mode


_TRANSIENT_CREATE_CODES = {"8433", "226645"}
_TRANSIENT_FAILURE_CODES = _TRANSIENT_CREATE_CODES | {"capacity_unavailable"}
_CAPACITY_ACTION = "CheckCompShareResourceCapacity"
_CAPACITY_MESSAGES = ("无可用规格库存", "无可用库存", "库存不足", "no available", "no inventory", "capacity unavailable")


def is_transient_capacity_failure(exc: BaseException) -> bool:
    """Classify provider failures that justify an automatic resource retry."""
    if not isinstance(exc, YouyunApiError):
        return False
    if str(exc.ret_code or "") in _TRANSIENT_CREATE_CODES:
        return True
    if exc.action != _CAPACITY_ACTION:
        return False
    message = str(exc.message or exc).casefold()
    return any(keyword.casefold() in message for keyword in _CAPACITY_MESSAGES)


def _enabled() -> bool:
    return os.environ.get("XUNTUI_YOUYUN_RESOURCE_HEALTH_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}


class YouyunResourceHealthRegistry:
    """Process-local cooldown state for provider candidate selection."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, cooldown_seconds: float | None = None) -> None:
        self.clock = clock
        configured = cooldown_seconds if cooldown_seconds is not None else float(os.environ.get("XUNTUI_YOUYUN_RESOURCE_HEALTH_COOLDOWN_SECONDS", "300"))
        self.cooldown_seconds = max(1.0, min(86_400.0, configured))
        self._entries: dict[tuple[str, ...], dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(candidate: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(candidate.get(field) or "").strip() for field in ("instanceMode", "region", "zone", "imageId", "gpuType", "chargeType"))

    def mark_failure(self, candidate: Mapping[str, Any], failure_code: str) -> None:
        if not _enabled() or str(failure_code) not in _TRANSIENT_FAILURE_CODES:
            return
        now = self.clock()
        with self._lock:
            self._entries[self.key(candidate)] = {"failureCode": str(failure_code), "blockedUntil": now + self.cooldown_seconds, "failedAt": now}

    def mark_success(self, candidate: Mapping[str, Any]) -> None:
        with self._lock:
            self._entries.pop(self.key(candidate), None)

    def metadata(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        if not _enabled():
            return {"healthStatus": "unknown"}
        key = self.key(candidate)
        with self._lock:
            entry = dict(self._entries.get(key) or {})
        blocked_until = float(entry.get("blockedUntil") or 0)
        if blocked_until <= self.clock():
            if entry:
                with self._lock:
                    self._entries.pop(key, None)
            return {"healthStatus": "unknown"}
        return {
            "healthStatus": "cooldown",
            "healthCooldownUntil": blocked_until,
            "failureCode": entry.get("failureCode"),
        }

    def is_blocked(self, candidate: Mapping[str, Any]) -> bool:
        return self.metadata(candidate).get("healthStatus") == "cooldown"

    def filter_candidates(self, candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            value = dict(candidate)
            value.update(self.metadata(value))
            if value.get("healthStatus") != "cooldown":
                result.append(value)
        return result


youyun_resource_health = YouyunResourceHealthRegistry()


__all__ = ["YouyunResourceHealthRegistry", "youyun_resource_health", "is_transient_capacity_failure"]


def find_uhost_fallback(manager: Any, resource: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a healthy same-region UHost candidate for automatic Pod runs."""
    if str(resource.get("instanceMode") or "pod") != "pod" or not bool(resource.get("fallbackEligible")) or str(resource.get("fallback") or "").casefold() in {"true", "1"}:
        return None
    describe = getattr(getattr(manager, "client", None), "describe_support_zones", None)
    if not callable(describe):
        return None
    region = str(resource.get("region") or "").strip()
    try:
        zones = describe(region)
    except Exception:
        return None
    for zone in zones or []:
        if zone.get("region") != region or not youyun_zone_supports_mode(zone, "uhost"):
            continue
        candidate = {**dict(resource), "instanceMode": "uhost", "zone": zone.get("zone"), "fallback": True}
        if not youyun_resource_health.is_blocked(candidate):
            candidate["fallbackReason"] = "Pod 创建接口暂不可用，已切换到 UHost 兜底。"
            return candidate
    return None


__all__ += ["find_uhost_fallback"]
