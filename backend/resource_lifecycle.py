from __future__ import annotations

"""Provider-neutral resource lease state machine used by long-running workloads.

The manager is deliberately small and in-memory: runtimes can persist the returned
dict alongside their task/service record, while tests can exercise failure and
watchdog semantics without provisioning a real cloud instance.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import time
import uuid
from collections.abc import Callable


class LeaseState(StrEnum):
    ACQUIRED = "acquired"
    RUNNING = "running"
    RELEASING = "releasing"
    RELEASED = "released"
    CLEANUP_FAILED = "cleanup_failed"


@dataclass
class ResourceLease:
    lease_id: str
    owner_id: str
    provider: str
    instance_id: str | None
    idempotency_key: str
    created_at: float
    last_heartbeat_at: float
    state: LeaseState = LeaseState.ACQUIRED
    release_requested_at: float | None = None
    release_confirmed_at: float | None = None
    cleanup_error: str | None = None
    release_reason: str | None = None


class ResourceLeaseManager:
    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._leases: dict[str, ResourceLease] = {}
        self._idempotency: dict[str, str] = {}

    def acquire(self, *, owner_id: str, provider: str, instance_id: str | None, idempotency_key: str | None = None) -> ResourceLease:
        key = idempotency_key or f"{owner_id}:{instance_id or 'none'}"
        existing_id = self._idempotency.get(key)
        if existing_id and existing_id in self._leases:
            return self._leases[existing_id]
        now = float(self._clock())
        lease = ResourceLease(
            lease_id=f"lease-{uuid.uuid4().hex[:16]}",
            owner_id=str(owner_id),
            provider=str(provider),
            instance_id=str(instance_id) if instance_id else None,
            idempotency_key=key,
            created_at=now,
            last_heartbeat_at=now,
        )
        self._leases[lease.lease_id] = lease
        self._idempotency[key] = lease.lease_id
        return lease

    def get(self, lease_id: str) -> ResourceLease:
        try:
            return self._leases[lease_id]
        except KeyError as exc:
            raise KeyError(f"未知资源租约: {lease_id}") from exc

    def heartbeat(self, lease_id: str) -> ResourceLease:
        lease = self.get(lease_id)
        if lease.state not in {LeaseState.RELEASED, LeaseState.CLEANUP_FAILED}:
            lease.last_heartbeat_at = float(self._clock())
        return lease

    def mark_running(self, lease_id: str) -> ResourceLease:
        lease = self.get(lease_id)
        if lease.state == LeaseState.ACQUIRED:
            lease.state = LeaseState.RUNNING
        return lease

    def request_release(self, lease_id: str, *, reason: str) -> ResourceLease:
        lease = self.get(lease_id)
        if lease.state in {LeaseState.RELEASED, LeaseState.CLEANUP_FAILED}:
            return lease
        if lease.state != LeaseState.RELEASING:
            lease.state = LeaseState.RELEASING
            lease.release_requested_at = float(self._clock())
            lease.release_reason = str(reason)[:240]
        return lease

    def mark_cleanup_failed(self, lease_id: str, error: str) -> ResourceLease:
        lease = self.get(lease_id)
        lease.state = LeaseState.CLEANUP_FAILED
        lease.cleanup_error = str(error)[:800]
        return lease

    def confirm_released(self, lease_id: str) -> ResourceLease:
        lease = self.get(lease_id)
        lease.state = LeaseState.RELEASED
        lease.release_confirmed_at = float(self._clock())
        lease.cleanup_error = None
        return lease

    def watchdog(self, *, stale_after_seconds: float = 600) -> list[ResourceLease]:
        now = float(self._clock())
        stale: list[ResourceLease] = []
        for lease in self._leases.values():
            if lease.state in {LeaseState.ACQUIRED, LeaseState.RUNNING} and now - lease.last_heartbeat_at >= stale_after_seconds:
                stale.append(self.request_release(lease.lease_id, reason="watchdog_stale_heartbeat"))
        return stale
