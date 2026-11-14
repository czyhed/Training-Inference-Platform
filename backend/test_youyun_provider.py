"""优云 Provider 单元测试：签名、参数拍平、实例解析。"""
from __future__ import annotations

import hashlib
import unittest

from backend.providers.youyun.client import YouyunClient, _flatten
from backend.providers.youyun.models import (
    assess_community_image_match,
    merge_community_youyun_images,
    rank_community_image_candidates,
    YouyunConfig,
    YouyunInstance,
    select_visual_inference_image,
    select_visual_training_image,
    recommend_image_candidates,
)
from backend.providers.youyun.ssh import YouyunSSHClient
from backend.providers.youyun.signer import encode_value, signature_base, sign


class SignerTest(unittest.TestCase):
    def test_official_doctest_vector(self) -> None:
        # 官方 SDK 文档样例：verify_ac("my_private_key", {"foo": "bar"})
        signed = sign({"foo": "bar"}, "my_private_key")
        self.assertEqual(signed["Signature"], "634edc1bb957c0d65e5ab5494cf3b7784fbc87af")

    def test_base_is_key_value_concat_then_private_key(self) -> None:
        base = signature_base({"b": "2", "a": "1"}, "sk")
        self.assertEqual(base, "a1b2sk")
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()
        self.assertEqual(sign({"b": "2", "a": "1"}, "sk")["Signature"], digest)


class CommunityImageCatalogTests(unittest.TestCase):
    def test_exposes_community_candidates_without_fake_billable_ids(self) -> None:
        rows = merge_community_youyun_images([
            {"image_id": "real-image", "name": "cuda132_torch2130_py312", "status": "Available"}
        ])
        names = {row["name"]: row for row in rows}
        self.assertIn("LLaMAFactory-WebUI", names)
        self.assertFalse(names["LLaMAFactory-WebUI"]["available"])
        self.assertNotIn("image_id", names["LLaMAFactory-WebUI"])

    def test_recommendation_prefers_workload_compatible_official_image(self) -> None:
        rows = recommend_image_candidates(
            [
                {"image_id": "community", "name": "LLaMAFactory-WebUI", "image_type": "Community", "status": "Available"},
                {"image_id": "torch280", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available"},
                {"image_id": "torch291", "name": "cuda128_torch291_py312", "image_type": "App", "status": "Available"},
            ],
            workload="vision_training",
            gpu_type="4090",
        )
        self.assertEqual(rows[0]["image_id"], "torch280")
        self.assertTrue(rows[0]["selectable"])

    def test_unverified_community_image_is_never_selectable(self) -> None:
        rows = recommend_image_candidates(
            [{"image_id": "community", "name": "LLaMAFactory-WebUI", "image_type": "Community", "status": "Available"}],
            workload="vision_training",
            gpu_type="4090",
        )
        self.assertFalse(rows[0]["selectable"])
        self.assertEqual(rows[0]["probeState"], "unverified")


class CommunityImageClientTests(unittest.TestCase):
    def _client(self) -> YouyunClient:
        config = YouyunConfig(public_key="pk", private_key="sk", region="cn-wlcb", zone="cn-wlcb-01")
        return YouyunClient(config)

    def test_describe_community_images_parses_groups_and_versions(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            return {
                "RetCode": 0,
                "ImageSet": [
                    {
                        "CompShareImageGroupId": "group-1",
                        "Name": "LLaMAFactory-WebUI",
                        "Tags": ["LLaMA-Factory", "Qwen2-VL"],
                        "ImageVersions": [
                            {
                                "CompShareImageId": "community-1",
                                "VersionName": "cuda132",
                                "Status": "Available",
                                "ImageType": "Community",
                                "Container": True,
                                "AutoStart": True,
                                "Price": 0,
                                "GpuTypes": ["4090"],
                            }
                        ],
                    }
                ],
            }

        client._post = fake_post  # type: ignore[method-assign]
        rows = client.describe_community_images(keyword="LLaMAFactory", limit=20)

        self.assertEqual(calls[0][0], "DescribeCommunityImages")
        self.assertEqual(calls[0][1]["Region"], "cn-wlcb")
        self.assertEqual(calls[0][1]["Zone"], "cn-wlcb-01")
        self.assertEqual(calls[0][1]["FuzzySearch"], "LLaMAFactory")
        self.assertEqual(rows[0]["image_id"], "community-1")
        self.assertEqual(rows[0]["group_id"], "group-1")
        self.assertEqual(rows[0]["name"], "LLaMAFactory-WebUI")
        self.assertEqual(rows[0]["version_name"], "cuda132")
        self.assertTrue(rows[0]["container"])
        self.assertEqual(rows[0]["supported_gpu_types"], ["4090"])

    def test_describe_community_images_accepts_flat_versions_and_empty_response(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            if len(calls) == 1:
                return {"RetCode": 0, "CommunityImageSet": [{"CompShareImageId": "community-2", "Name": "Qwen VL", "Status": "Reviewing"}]}
            return {"RetCode": 0}

        client._post = fake_post  # type: ignore[method-assign]
        self.assertEqual(client.describe_community_images(page=1, page_size=10)[0]["image_id"], "community-2")
        self.assertEqual(client.describe_community_images(page=2, page_size=10), [])

    def test_describe_community_images_parses_live_compshare_group_data(self) -> None:
        client = self._client()

        def fake_post(action: str, params: dict) -> dict:
            return {
                "RetCode": 0,
                "CompshareImageGroup": [
                    {
                        "GroupId": "group-live",
                        "ImageName": "LLaMA-Factory 视觉训练",
                        "ImageDesc": "Qwen-VL LoRA",
                        "Data": [
                            {
                                "CompShareImageId": "compshareImage-live",
                                "Name": "LLaMA-Factory 视觉训练",
                                "VersionName": "v1.2",
                                "Status": "Available",
                                "Container": "True",
                                "AutoStart": True,
                                "Price": 0.2,
                                "SupportedGpuTypes": ["4090", "4090_48G"],
                                "Tags": ["LLaMA-Factory", "Qwen-VL"],
                            }
                        ],
                    }
                ],
            }

        client._post = fake_post  # type: ignore[method-assign]
        row = client.describe_community_images()[0]

        self.assertEqual(row["image_id"], "compshareImage-live")
        self.assertEqual(row["group_id"], "group-live")
        self.assertEqual(row["version_name"], "v1.2")
        self.assertEqual(row["supported_gpu_types"], ["4090", "4090_48G"])
        self.assertTrue(row["container"])

    def test_describe_model_repository_models_uses_official_parameters(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            return {
                "RetCode": 0,
                "Models": [
                    {
                        "Name": "Qwen2-VL-2B",
                        "Path": "/models/qwen2-vl-2b",
                        "Tag": "VL,Qwen",
                        "Size": "4GB",
                        "CreateTime": 1712563200,
                    }
                ],
            }

        client._post = fake_post  # type: ignore[method-assign]
        rows = client.describe_model_repository_models(name="qwen", tags="VL")

        self.assertEqual(calls, [("DescribeModelRepositoryModels", {"Region": "cn-wlcb", "name": "qwen", "tags": "VL"})])
        self.assertEqual(rows[0]["name"], "Qwen2-VL-2B")
        self.assertEqual(rows[0]["path"], "/models/qwen2-vl-2b")
        self.assertEqual(rows[0]["tags"], ["VL", "Qwen"])
        self.assertEqual(rows[0]["size"], "4GB")
        self.assertEqual(rows[0]["create_time"], 1712563200)
        self.assertEqual(rows[0]["raw"]["Name"], "Qwen2-VL-2B")

    def test_create_instance_includes_machine_type_and_cpu_platform(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            return {"RetCode": 0, "UHostIds": ["uhost-test"]}

        client._post = fake_post  # type: ignore[method-assign]
        ids = client.create_instance(
            name="cppt-test",
            gpu=1,
            cpu=16,
            memory_gb=64,
            image_id="image-test",
            gpu_type="4090",
            charge_type="Postpay",
        )

        self.assertEqual(ids, ["uhost-test"])
        self.assertEqual(calls[0][0], "CreateCompShareInstance")
        self.assertEqual(calls[0][1]["MachineType"], "G")
        self.assertEqual(calls[0][1]["MinimalCpuPlatform"], "Auto")

    def test_encode_value_special_cases(self) -> None:
        self.assertEqual(encode_value(True), "true")
        self.assertEqual(encode_value(False), "false")
        self.assertEqual(encode_value(2.0), "2")
        self.assertEqual(encode_value(2.5), "2.5")
        self.assertEqual(encode_value("任务 名称"), "任务 名称")
        self.assertEqual(encode_value(100), "100")


class FlattenTest(unittest.TestCase):
    def test_flatten_nested_arrays(self) -> None:
        flat = _flatten({"Disks": [{"IsBoot": True, "Size": 100}], "Region": "cn-wlcb"})
        self.assertEqual(
            flat,
            {"Disks.0.IsBoot": True, "Disks.0.Size": 100, "Region": "cn-wlcb"},
        )


class InstanceParseTest(unittest.TestCase):
    def test_parses_ssh_command(self) -> None:
        instance = YouyunInstance.from_api(
            {
                "UHostId": "uhost-1",
                "State": "Running",
                "SshLoginCommand": "ssh root@1.2.3.4 -p 60022",
                "Password": "cGFzc3dvcmQ=",
                "GPU": 1,
                "GpuType": "4090",
                "Memory": 65536,
            }
        )
        self.assertTrue(instance.running)
        self.assertEqual(instance.ip, "1.2.3.4")
        self.assertEqual(instance.ssh_port, 60022)
        self.assertEqual(instance.ssh_user, "root")
        self.assertEqual(instance.memory_mb, 65536)

    def test_without_ssh_info(self) -> None:
        instance = YouyunInstance.from_api({"UHostId": "uhost-2", "State": "Install"})
        self.assertFalse(instance.running)
        self.assertIsNone(instance.ip)
        self.assertEqual(instance.ssh_user, "root")

    def test_parses_port_first_ssh_command(self) -> None:
        # 优云实际返回顺序：ssh -p 23 root@1.2.3.4
        instance = YouyunInstance.from_api({"UHostId": "uhost-3", "SshLoginCommand": "ssh -p 23 root@117.50.183.121"})
        self.assertEqual(instance.ip, "117.50.183.121")
        self.assertEqual(instance.ssh_port, 23)
        self.assertEqual(instance.ssh_user, "root")


class SSHClientTest(unittest.TestCase):
    def test_explicit_reconnect_preserves_credentials(self) -> None:
        class Session:
            def get_transport(self):
                return self

            def is_active(self):
                return True

            def close(self):
                return None

        class Factory:
            def __init__(self):
                self.calls: list[dict] = []

            def connect(self, **kwargs):
                self.calls.append(kwargs)
                return Session()

        factory = Factory()
        client = YouyunSSHClient(transport=factory)
        client.connect("1.2.3.4", 23, "root", "pass")

        client.reconnect()

        self.assertEqual(len(factory.calls), 2)
        self.assertEqual(factory.calls[1], factory.calls[0])

    def test_reconnects_before_command_when_transport_is_inactive(self) -> None:
        class Channel:
            def recv_exit_status(self):
                return 0

        class Stream:
            channel = Channel()

            def read(self):
                return b"ok"

        class Stdin:
            def close(self):
                return None

        class Session:
            def __init__(self, active):
                self.active = active

            def get_transport(self):
                return self

            def is_active(self):
                return self.active

            def exec_command(self, command, timeout):
                return Stdin(), Stream(), Stream()

            def close(self):
                self.active = False

        class Factory:
            def __init__(self):
                self.calls = 0

            def connect(self, **kwargs):
                self.calls += 1
                return Session(True)

        factory = Factory()
        client = YouyunSSHClient(transport=factory)
        client.connect("1.2.3.4", 23, "root", "pass")
        client._client.active = False
        code, output = client.run("echo ok")
        self.assertEqual(code, 0)
        self.assertEqual(output, "okok")
        self.assertEqual(factory.calls, 2)

    def test_reconnects_when_session_dies_during_channel_open(self) -> None:
        class Channel:
            def recv_exit_status(self):
                return 0

        class Stream:
            channel = Channel()

            def read(self):
                return b"ok"

        class Stdin:
            def close(self):
                return None

        class Session:
            def __init__(self, fail):
                self.fail = fail

            def get_transport(self):
                return self

            def is_active(self):
                return True

            def exec_command(self, command, timeout):
                if self.fail:
                    raise RuntimeError("SSH session not active")
                return Stdin(), Stream(), Stream()

            def close(self):
                return None

        class Factory:
            def __init__(self):
                self.calls = 0

            def connect(self, **kwargs):
                self.calls += 1
                return Session(self.calls == 1)

        factory = Factory()
        client = YouyunSSHClient(transport=factory)
        client.connect("1.2.3.4", 23, "root", "pass")
        code, output = client.run("echo ok")
        self.assertEqual(code, 0)
        self.assertEqual(output, "okok")
        self.assertEqual(factory.calls, 2)


class DiscoveryTest(unittest.TestCase):
    def test_vision_training_community_images_rank_verified_matching_environment_first(self) -> None:
        images = [
            {"image_id": "video", "name": "Minimax-H3 video", "status": "Available", "supported_gpu_types": ["4090"]},
            {"image_id": "vision", "name": "LLaMAFactory-WebUI", "version_name": "v1.0", "status": "Available", "tags": ["Qwen2.5-VL", "LoRA"], "supported_gpu_types": ["4090"]},
        ]

        ranked = rank_community_image_candidates(
            images,
            workload="vision_training",
            gpu_type="4090",
            verification_by_id={"vision": {"state": "verified"}},
        )

        self.assertEqual(ranked[0]["image_id"], "vision")
        self.assertTrue(ranked[0]["compatible"])
        self.assertTrue(ranked[0]["selectable"])
        self.assertFalse(ranked[1]["compatible"])

    def test_unverified_matching_community_image_requires_paid_probe_before_selection(self) -> None:
        result = assess_community_image_match(
            {"image_id": "vision", "name": "LLaMAFactory-WebUI", "status": "Available", "tags": ["Qwen2-VL"], "supported_gpu_types": ["4090"]},
            workload="vision_training",
            gpu_type="4090",
            verification_state="unverified",
        )

        self.assertTrue(result["compatible"])
        self.assertFalse(result["selectable"])
        self.assertIn("真实实例探针", result["recommendation_reason"])

    def test_image_generation_match_rejects_incompatible_gpu(self) -> None:
        result = assess_community_image_match(
            {"image_id": "sdxl", "name": "SDXL Diffusers", "status": "Available", "tags": ["Diffusers", "LoRA"], "supported_gpu_types": ["A100"]},
            workload="image_generation",
            gpu_type="4090",
            verification_state="verified",
        )

        self.assertFalse(result["compatible"])
        self.assertFalse(result["selectable"])
        self.assertIn("4090", result["recommendation_reason"])

    def test_sdxl_training_matches_ai_toolkit_lora_image(self) -> None:
        result = assess_community_image_match(
            {
                "image_id": "community-sdxl",
                "name": "SDXL 系列衍生模型训练 AI-Toolkit",
                "status": "Available",
                "tags": ["SD", "Lora训练"],
                "supported_gpu_types": ["4090"],
            },
            workload="image_generation_training",
            gpu_type="4090",
            verification_state="unverified",
        )
        self.assertTrue(result["compatible"])
        self.assertFalse(result["selectable"])

    def test_sdxl_training_rejects_other_workloads(self) -> None:
        for name in ("MiniMax video", "Qwen2.5-VL LLaMAFactory", "ComfyUI 绘图工作台"):
            result = assess_community_image_match(
                {"image_id": name, "name": name, "status": "Available", "supported_gpu_types": ["4090"]},
                workload="image_generation_training",
                gpu_type="4090",
                verification_state="verified",
            )
            self.assertFalse(result["compatible"], name)


    def test_vision_training_rejects_inference_only_vllm_image(self) -> None:
        result = assess_community_image_match(
            {"image_id": "qwen-vllm", "name": "Qwen-vLLM-Series", "status": "Available", "description": "Qwen2.5-VL 多模态模型 WebUI"},
            workload="vision_training",
            gpu_type="4090",
            verification_state="verified",
        )

        self.assertFalse(result["compatible"])

    def test_sdxl_training_rejects_qwen_image_specific_environment(self) -> None:
        result = assess_community_image_match(
            {"image_id": "qwen-image", "name": "AiToolKit", "status": "Available", "tags": ["Qwen-image", "LoRA训练"]},
            workload="image_generation",
            gpu_type="4090",
            verification_state="verified",
        )

        self.assertFalse(result["compatible"])

    def test_visual_inference_image_prefers_vllm_application_image(self) -> None:
        images = [
            {"image_id": "torch-280", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available"},
            {"image_id": "vllm-025", "name": "vLLM v0.25.1", "image_type": "App", "status": "Available"},
            {"image_id": "vllm-old", "name": "vLLM v0.12.0", "image_type": "App", "status": "Available"},
        ]

        selected = select_visual_inference_image(images)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["image_id"], "vllm-025")

    def test_visual_training_image_prefers_latest_torch_below_2_9(self) -> None:
        images = [
            {"image_id": "torch-291", "name": "cuda128_torch291_py312", "image_type": "App", "status": "Available"},
            {"image_id": "torch-280", "name": "cuda128_torch280_py312", "image_type": "App", "status": "Available"},
            {"image_id": "torch-270", "name": "cuda126_torch270_py311", "image_type": "App", "status": "Available"},
            {"image_id": "windows", "name": "Windows 2019 64位", "image_type": "System", "status": "Available"},
        ]

        selected = select_visual_training_image(images, configured_image_id="torch-291")

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["image_id"], "torch-280")

    def _client(self) -> YouyunClient:
        config = YouyunConfig(public_key="pk", private_key="sk", region="cn-wlcb", zone="cn-wlcb-01")
        return YouyunClient(config)

    def test_describe_support_zones(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            return {"RetCode": 0, "ZoneInfo": [{"Region": "cn-wlcb", "Zone": "cn-wlcb-01", "Describe": "乌兰察布一区", "IsPod": False}]}

        client._post = fake_post  # type: ignore[method-assign]
        zones = client.describe_support_zones()
        self.assertEqual(calls[0][0], "DescribeCompShareSupportZone")
        self.assertEqual(zones[0]["zone"], "cn-wlcb-01")
        self.assertFalse(zones[0]["is_pod"])

    def test_describe_images_parses_and_filters_region(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            return {
                "RetCode": 0,
                "ImageSet": [{"CompShareImageId": "img-1", "Name": "CUDA 12.4 PyTorch", "ImageType": "App", "Container": "True", "Author": "official", "ImageOwnerAlias": "Official", "Softwares": {"OsVersion": "Ubuntu"}, "Zone": "cn-wlcb-03", "ImageSupportZone": ["cn-wlcb-03"], "Status": "Available", "Tags": ["pytorch"], "Description": ""}],
            }

        client._post = fake_post  # type: ignore[method-assign]
        images = client.describe_images()
        self.assertEqual(calls[0][1]["Region"], "cn-wlcb")
        self.assertEqual(images[0]["image_id"], "img-1")
        self.assertEqual(images[0]["tags"], ["pytorch"])
        self.assertEqual(images[0]["container"], "True")
        self.assertEqual(images[0]["author"], "official")
        self.assertEqual(images[0]["image_owner_alias"], "Official")
        self.assertEqual(images[0]["softwares"], {"OsVersion": "Ubuntu"})
        self.assertEqual(images[0]["zone"], "cn-wlcb-03")
        self.assertEqual(images[0]["image_support_zone"], ["cn-wlcb-03"])
        self.assertEqual(calls[0][1]["Zone"], client.config.zone)
        client.describe_images(use_config_zone=False)
        self.assertNotIn("Zone", calls[-1][1])
        client.describe_images(zone="cn-bj2-03", use_config_zone=False)
        self.assertEqual(calls[-1][1]["Zone"], "cn-bj2-03")

    def test_get_instance_price_selects_requested_charge_type(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            return {
                "RetCode": 0,
                "PriceDetails": [
                    {"ChargeType": "Month", "Instance": 1068, "SystemDisks": 120},
                    {"ChargeType": "Postpay", "Instance": 5.2, "SystemDisks": 0.8},
                ],
            }

        client._post = fake_post  # type: ignore[method-assign]
        price = client.get_instance_price(gpu=1, cpu=16, memory_gb=64)
        self.assertEqual(calls[0][0], "GetCompShareInstancePrice")
        self.assertEqual(calls[0][1]["Memory"], 64 * 1024)
        self.assertEqual(price.charge_type, "Postpay")
        self.assertAlmostEqual(price.total, 6.0)

    def test_capacity_and_price_accept_explicit_zone(self) -> None:
        client = self._client()
        calls: list[tuple[str, dict]] = []

        def fake_post(action: str, params: dict) -> dict:
            calls.append((action, params))
            if action == "CheckCompShareResourceCapacity":
                return {"RetCode": 0, "Specs": []}
            return {"RetCode": 0, "PriceDetails": [{"ChargeType": "Postpay", "Instance": 1}]}

        client._post = fake_post  # type: ignore[method-assign]
        client.check_resource_capacity(zone="cn-sh2-02", gpu_type="4090", image_id="img")
        client.get_instance_price(gpu=1, cpu=16, memory_gb=64, zone="cn-sh2-02")

        self.assertEqual(calls[0][1]["Zone"], "cn-sh2-02")
        self.assertEqual(calls[1][1]["Zone"], "cn-sh2-02")


if __name__ == "__main__":
    unittest.main()
