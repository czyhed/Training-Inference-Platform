"""WP1: every platform list is served from server storage and owner uses gateway identity.

The test starts with a fresh HTTP client (no cookies), sends only the headers
the application gateway injects, and checks that records created on the server
remain visible.  It also asserts the decoded identity (``password:xshipai``)
lands in owner/audit fields and that a browser-supplied ``X-User-Id`` alone is
not accepted as authentication.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

GATEWAY_HEADERS = {
    "x-control-user-id": "password%3Axshipai",
    "x-control-username": "xshipai",
}
EXPECTED_IDENTITY = "password:xshipai"


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/datasets",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "query_string": b"",
        }
    )


class PlatformRecordPersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        env = {
            "XUNTUI_STORAGE_BACKEND": "sqlite",
            "XUNTUI_DATABASE_PATH": str(root / "app.db"),
            "XUNTUI_RUNTIME_ROOT": str(root / "runtime"),
            "XUNTUI_DATASET_ROOT": str(root / "datasets"),
            "XUNTUI_ARTIFACT_ROOT": str(root / "artifacts"),
        }
        with patch.dict(os.environ, env):
            from backend import app as app_module

        cls.app = app_module
        # The HTTP middleware/lifespan normally initialises these stores at
        # start-up; this test drives routes directly from a TestClient.
        cls.app.audit_store.initialize()
        # When the suite already imported backend.app, keep using its configured
        # server-side storage; only ensure the dataset root is writable.
        cls.dataset_root = Path(app_module.DATASET_ROOT)
        cls.dataset_root.mkdir(parents=True, exist_ok=True)
        cls.created_ids: list[str] = []

    @classmethod
    def tearDownClass(cls) -> None:
        for dataset_id in cls.created_ids:
            try:
                cls.app.store.delete_dataset(dataset_id)
            except Exception:
                pass
        try:
            cls.temp.cleanup()
        except OSError:
            # Windows may still hold the SQLite file briefly after the tests.
            pass

    def setUp(self) -> None:
        self.auth = patch.object(self.app, "AUTH_MODE", "required")
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.source_id = self._seed_dataset()

    def _seed_dataset(self) -> str:
        dataset_id = f"ds-wp1-{uuid.uuid4().hex[:10]}"
        directory = self.dataset_root / dataset_id
        directory.mkdir(parents=True, exist_ok=True)
        train_path = directory / "train.jsonl"
        train_path.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "你好"},
                        {"role": "assistant", "content": "你好，有什么可以帮你？"},
                    ]
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.app.store.create_dataset(
            {
                "id": dataset_id,
                "name": "WP1 服务端记录验证",
                "type": "文本生成",
                "structure": "SFT",
                "state": "ready",
                "sampleCount": 1,
                "paths": {"trainPath": str(train_path)},
                "validationMessage": "校验通过",
                "validationReport": {"errors": [], "warnings": []},
                "warningCount": 0,
                "owner": "seed",
                "builtin": False,
            }
        )
        self.created_ids.append(dataset_id)
        return dataset_id

    def _client(self) -> TestClient:
        """A brand-new session: no cookies, only the gateway headers.

        TestClient is used without the lifespan context on purpose: start-up
        runtimes are process-wide and other backend tests own their lifecycle.
        """
        client = TestClient(self.app.app)
        self.addCleanup(client.close)
        return client

    def test_gateway_identity_is_url_decoded(self) -> None:
        identity = self.app.gateway_user_from_request(_request(GATEWAY_HEADERS))
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity["sub"], EXPECTED_IDENTITY)
        self.assertEqual(identity["name"], "xshipai")

        plain = self.app.gateway_user_from_request(
            _request({"x-control-user-id": "password:xshipai"})
        )
        assert plain is not None
        self.assertEqual(plain["sub"], EXPECTED_IDENTITY)

    def test_fresh_session_sees_server_records_and_model_lists(self) -> None:
        client = self._client()
        datasets = client.get("/api/datasets", headers=GATEWAY_HEADERS)
        self.assertEqual(datasets.status_code, 200, datasets.text)
        ids = {item["id"] for item in datasets.json()}
        self.assertIn(self.source_id, ids)

        platform = client.get(
            "/api/platform/datasets",
            params={"query": self.source_id},
            headers=GATEWAY_HEADERS,
        )
        self.assertEqual(platform.status_code, 200, platform.text)
        payload = platform.json()
        self.assertGreaterEqual(payload["total"], 1)
        self.assertIn(self.source_id, {item["id"] for item in payload["items"]})

        for path in ("/api/platform/tasks", "/api/platform/services", "/api/model-assets"):
            response = client.get(path, headers=GATEWAY_HEADERS)
            self.assertEqual(response.status_code, 200, f"{path}: {response.text}")

    def test_clone_owner_and_audit_use_decoded_gateway_identity(self) -> None:
        client = self._client()
        cloned = client.post(
            f"/api/datasets/{self.source_id}/clone", headers=GATEWAY_HEADERS
        )
        self.assertEqual(cloned.status_code, 201, cloned.text)
        clone = cloned.json()
        self.created_ids.append(clone["id"])
        self.assertEqual(clone["owner"], EXPECTED_IDENTITY)

        # A second fresh session sees the clone created by the first one.
        other = self._client()
        listed = other.get("/api/datasets", headers=GATEWAY_HEADERS)
        self.assertIn(clone["id"], {item["id"] for item in listed.json()})

        published = client.post(
            f"/api/datasets/{self.source_id}/publish", headers=GATEWAY_HEADERS
        )
        self.assertEqual(published.status_code, 200, published.text)

        logs = client.get("/api/audit-logs", headers=GATEWAY_HEADERS)
        self.assertEqual(logs.status_code, 200, logs.text)
        publish_rows = [
            row
            for row in logs.json()
            if row["action"] == "publish" and row["object_id"] == self.source_id
        ]
        self.assertTrue(publish_rows, "publish audit row missing")
        self.assertEqual(publish_rows[0]["actor_id"], EXPECTED_IDENTITY)

    def test_browser_supplied_x_user_id_is_not_authentication(self) -> None:
        client = self._client()
        response = client.get(
            "/api/datasets", headers={"x-user-id": "password%3Axshipai"}
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_records_live_in_server_storage_not_process_memory(self) -> None:
        if self.app.STORAGE_BACKEND != "sqlite":
            self.skipTest("SQLite-only assertion; production uses PostgreSQL.")
        from backend.store import TaskStore

        # A brand-new store instance on the same server-side database sees the
        # record, so a restarted backend or another browser is unaffected.
        fresh = TaskStore(Path(self.app.DATABASE_PATH))
        self.assertIn(self.source_id, {item["id"] for item in fresh.list_datasets()})


if __name__ == "__main__":
    unittest.main()
