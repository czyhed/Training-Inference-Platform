from __future__ import annotations

import unittest
from scripts.check_release_idle import ensure_idle


class ReleaseIdleTest(unittest.TestCase):
    def read(self, path):
        if path == "/health":
            return {"status": "ok", "activeTaskIds": [], "inference": "idle"}
        if "?" in path:
            return {"items": [{"id": "ft-test", "state": "completed"}], "total": 1}
        return {"artifacts": [{"type": "adapter", "syncState": "available"}]}

    def test_completed_workloads_are_safe(self):
        self.assertEqual(ensure_idle(self.read)["/tasks"], 1)

    def test_active_or_unknown_workload_blocks_release(self):
        for state in ("running", "queued", "starting", None):
            with self.subTest(state=state):
                def read(path):
                    value = self.read(path)
                    if "items" in value:
                        value["items"][0]["state"] = state
                    return value
                with self.assertRaisesRegex(RuntimeError, "workload"):
                    ensure_idle(read)

    def test_artifact_sync_blocks_release(self):
        def read(path):
            value = self.read(path)
            if "artifacts" in value:
                value["artifacts"][0]["syncState"] = "syncing"
            return value
        with self.assertRaisesRegex(RuntimeError, "sync in progress"):
            ensure_idle(read)

    def test_active_workload_on_later_page_blocks_release(self):
        def read(path):
            value = self.read(path)
            if "?" in path:
                value["hasNext"] = "page=1&" in path
                if "page=2&" in path:
                    value["items"][0]["state"] = "running"
            return value
        with self.assertRaisesRegex(RuntimeError, "workload"):
            ensure_idle(read)
