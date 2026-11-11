from __future__ import annotations

import unittest

from scripts.run_backend import backend_environment


class RunBackendEnvironmentTests(unittest.TestCase):
    def test_local_backend_defaults_to_isolated_storage(self) -> None:
        environment = backend_environment({})

        self.assertEqual(environment["XUNTUI_STORAGE_BACKEND"], "sqlite")
        self.assertEqual(environment["XUNTUI_SHARED_STORAGE_ENABLED"], "false")
        self.assertEqual(environment["XUNTUI_INFERENCE_AUTO_RECOVERY"], "false")

    def test_local_backend_allows_explicit_shared_storage_override(self) -> None:
        environment = backend_environment(
            {
                "XUNTUI_LOCAL_STORAGE_BACKEND": "postgres",
                "XUNTUI_LOCAL_SHARED_STORAGE_ENABLED": "true",
                "XUNTUI_LOCAL_INFERENCE_AUTO_RECOVERY": "true",
            }
        )

        self.assertEqual(environment["XUNTUI_STORAGE_BACKEND"], "postgres")
        self.assertEqual(environment["XUNTUI_SHARED_STORAGE_ENABLED"], "true")
        self.assertEqual(environment["XUNTUI_INFERENCE_AUTO_RECOVERY"], "true")


if __name__ == "__main__":
    unittest.main()
