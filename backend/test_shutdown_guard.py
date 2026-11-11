from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.shutdown_guard import collect_persisted_handles


def seed_services(db_path: Path, rows: list[tuple[str, str, str]]) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE inference_services (
                id TEXT PRIMARY KEY,
                name TEXT,
                state TEXT,
                config TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO inference_services (id, name, state, config) VALUES (?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


class CollectPersistedHandlesTests(unittest.TestCase):
    def test_collects_running_service_handles_with_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "training.db"
            seed_services(
                db_path,
                [
                    (
                        "svc-1",
                        "视觉服务",
                        "running",
                        json.dumps(
                            {
                                "workloadType": "vision_language",
                                "remoteRuntime": {
                                    "instanceId": "uhost-1",
                                    "region": "cn-wlcb",
                                    "zone": "cn-wlcb-01",
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ),
                    (
                        "svc-2",
                        "重复实例",
                        "starting",
                        json.dumps({"remoteRuntime": {"instanceId": "uhost-1"}}),
                    ),
                    (
                        "svc-3",
                        "已停止",
                        "stopped",
                        json.dumps({"remoteRuntime": {"instanceId": "uhost-2"}}),
                    ),
                    ("svc-4", "无句柄", "running", json.dumps({"provider": "youyun"})),
                    ("svc-5", "坏配置", "recovering", "{not-json"),
                ],
            )

            handles = collect_persisted_handles(db_path)

            self.assertEqual(len(handles), 1)
            self.assertEqual(handles[0]["instanceId"], "uhost-1")
            self.assertEqual(handles[0]["region"], "cn-wlcb")

    def test_missing_database_returns_empty(self) -> None:
        self.assertEqual(collect_persisted_handles(Path("Z:/nowhere/training.db")), [])


if __name__ == "__main__":
    unittest.main()
