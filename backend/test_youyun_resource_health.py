from __future__ import annotations

import unittest

from backend.providers.youyun.resource_health import YouyunResourceHealthRegistry


class ResourceHealthTests(unittest.TestCase):
    def test_transient_create_failure_blocks_candidate_until_cooldown_expires(self) -> None:
        now = [100.0]
        registry = YouyunResourceHealthRegistry(clock=lambda: now[0], cooldown_seconds=30)
        candidate = {"instanceMode": "pod", "region": "cn-wlcb", "zone": "cn-wlcb-01", "imageId": "img", "gpuType": "4090", "chargeType": "Postpay"}

        registry.mark_failure(candidate, "226645")
        self.assertTrue(registry.is_blocked(candidate))
        self.assertEqual(registry.filter_candidates([candidate]), [])
        now[0] = 131.0
        self.assertFalse(registry.is_blocked(candidate))

    def test_success_clears_previous_cooldown(self) -> None:
        registry = YouyunResourceHealthRegistry(clock=lambda: 100.0, cooldown_seconds=30)
        candidate = {"instanceMode": "uhost", "region": "cn-bj2", "zone": "cn-bj2-03", "imageId": "img", "gpuType": "4090", "chargeType": "Postpay"}
        registry.mark_failure(candidate, "8433")
        registry.mark_success(candidate)
        self.assertFalse(registry.is_blocked(candidate))

    def test_health_metadata_exposes_cooldown_reason(self) -> None:
        registry = YouyunResourceHealthRegistry(clock=lambda: 100.0, cooldown_seconds=30)
        candidate = {"instanceMode": "pod", "region": "cn-wlcb", "zone": "cn-wlcb-01", "imageId": "img", "gpuType": "4090", "chargeType": "Postpay"}
        registry.mark_failure(candidate, "226645")
        metadata = registry.metadata(candidate)
        self.assertEqual(metadata["healthStatus"], "cooldown")
        self.assertEqual(metadata["failureCode"], "226645")


if __name__ == "__main__":
    unittest.main()
