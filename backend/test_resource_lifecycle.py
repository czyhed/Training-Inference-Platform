from __future__ import annotations

import time
import unittest

from backend.resource_lifecycle import LeaseState, ResourceLease, ResourceLeaseManager


class ResourceLeaseTests(unittest.TestCase):
    def test_failed_work_enters_releasing_and_only_confirmed_cleanup_is_released(self) -> None:
        manager = ResourceLeaseManager(clock=lambda: 100.0)
        lease = manager.acquire(owner_id="vt-1", provider="youyun", instance_id="uhost-1")

        self.assertEqual(lease.state, LeaseState.ACQUIRED)
        manager.mark_running(lease.lease_id)
        manager.request_release(lease.lease_id, reason="training_failed")
        self.assertEqual(manager.get(lease.lease_id).state, LeaseState.RELEASING)

        failed = manager.mark_cleanup_failed(lease.lease_id, "provider timeout")
        self.assertEqual(failed.state, LeaseState.CLEANUP_FAILED)
        self.assertNotEqual(failed.state, LeaseState.RELEASED)

        released = manager.confirm_released(lease.lease_id)
        self.assertEqual(released.state, LeaseState.RELEASED)
        self.assertEqual(released.release_confirmed_at, 100.0)

    def test_duplicate_release_is_idempotent_and_does_not_change_handle(self) -> None:
        manager = ResourceLeaseManager(clock=lambda: 42.0)
        lease = manager.acquire(owner_id="svc-1", provider="youyun", instance_id="uhost-2")
        first = manager.request_release(lease.lease_id, reason="stop")
        second = manager.request_release(lease.lease_id, reason="stop-again")

        self.assertEqual(first.lease_id, second.lease_id)
        self.assertEqual(second.instance_id, "uhost-2")
        self.assertEqual(second.state, LeaseState.RELEASING)

    def test_watchdog_marks_stale_running_lease_for_release(self) -> None:
        now = [100.0]
        manager = ResourceLeaseManager(clock=lambda: now[0])
        lease = manager.acquire(owner_id="igt-1", provider="youyun", instance_id="uhost-3")
        manager.mark_running(lease.lease_id)
        now[0] = 100.0 + 601.0

        stale = manager.watchdog(stale_after_seconds=600)
        self.assertEqual([item.lease_id for item in stale], [lease.lease_id])
        self.assertEqual(manager.get(lease.lease_id).state, LeaseState.RELEASING)


if __name__ == "__main__":
    unittest.main()
