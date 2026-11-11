from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.providers.youyun.diffusers import YouyunDiffusersProvider
from backend.providers.youyun.models import YouyunApiError
from backend.providers.youyun.resource_health import is_transient_capacity_failure


class FakeInstance:
    uhost_id = "uhost-img-1"
    ip = "10.0.0.8"
    ssh_port = 22
    ssh_user = "root"
    password_b64 = ""


class FakeManager:
    def __init__(self, cleanup: dict | None = None) -> None:
        self.created: list[dict] = []
        self.cleaned: list[str] = []
        self.cleanup = cleanup or {"terminated": True}

    def create_one_shot(self, task_id, **kwargs):
        self.created.append({"task_id": task_id, **kwargs})
        return FakeInstance()

    def ssh_credentials(self, instance):
        return {"host": instance.ip, "port": instance.ssh_port, "user": instance.ssh_user, "password": "secret"}

    def cleanup_after_task(self, instance_id, stop_first=False, region=None, zone=None):
        self.cleaned.append(instance_id)
        return self.cleanup


class FakeSSH:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.files: dict[str, str] = {}
        self.status_reads = 0
        self.closed = False

    def connect(self, host, port, user, password):
        return None

    def mkdirs(self, path):
        self.commands.append(f"mkdirs {path}")

    def put_text(self, text, path):
        self.files[path] = text

    def put_file(self, local, remote):
        self.files[remote] = Path(local).read_text(encoding="utf-8")

    def get_file(self, remote, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_text("png", encoding="utf-8")

    def run(self, command, timeout=120, check=True):
        self.commands.append(command)
        if "import torch, diffusers" in command:
            return 0, "/opt/conda/bin/python\n"
        if "base-model-path" in command:
            return 0, "base-model-path:/models/flux\n"
        if "status.json" in command and "cat" in command:
            self.status_reads += 1
            if self.status_reads == 1:
                return 0, '{"state":"running","progress":45,"step":9,"totalSteps":20}\n'
            return 0, '{"state":"completed","progress":100,"results":[{"id":"0","fileName":"0.png"}]}\n'
        if "kill -0" in command:
            return 0, ""
        return 0, ""

    def close(self):
        self.closed = True


class EarlyExitSSH(FakeSSH):
    def run(self, command, timeout=120, check=True):
        if "kill -0" in command:
            return 1, ""
        return super().run(command, timeout, check)


class InvalidDownloadSSH(FakeSSH):
    def get_file(self, remote, local):
        raise FileNotFoundError(remote)


class RuntimeUnavailableSSH(FakeSSH):
    def run(self, command, timeout=120, check=True):
        if "import torch, diffusers" in command:
            return 1, ""
        return super().run(command, timeout, check)


class FlakyConnectSSH(FakeSSH):
    attempts = 0

    def connect(self, host, port, user, password):
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise ConnectionError("SSH 服务尚未就绪")
        return None


class TorchOnlyRuntimeSSH(FakeSSH):
    def run(self, command, timeout=120, check=True):
        self.commands.append(command)
        if "import torch, diffusers" in command and "pip install" not in command:
            return 42, ""
        if "import torch" in command and "diffusers" not in command:
            return 0, "/usr/local/miniconda3/envs/py312/bin/python\n"
        if "pip install" in command:
            return 0, "installed\n"
        return super().run(command, timeout, check)


class ModelUnavailableSSH(FakeSSH):
    def run(self, command, timeout=120, check=True):
        if "base-model-path" in command:
            return 0, "base-model-error:no matching complete model\n"
        return super().run(command, timeout, check)


class InstanceStartFailureManager(FakeManager):
    def create_one_shot(self, task_id, **kwargs):
        raise RuntimeError("CreateCompShareInstance password=super-secret command=do-not-expose")


class YouyunDiffusersTests(unittest.TestCase):
    def test_capacity_inventory_error_is_transient(self) -> None:
        error = YouyunApiError(
            "CheckCompShareResourceCapacity",
            None,
            "GPU 5090 在 cn-wlcb-01 无可用规格库存。",
        )
        self.assertTrue(is_transient_capacity_failure(error))

    def test_auto_mode_releases_failed_pod_before_uhost_fallback(self) -> None:
        class Manager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.client = type("Client", (), {"describe_support_zones": lambda _self, region=None: [{"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False}]})()
                self.calls: list[dict] = []

            def create_one_shot(self, task_id, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    callback = kwargs.get("on_created")
                    if callable(callback):
                        callback("pod-image-leak")
                    raise YouyunApiError("CreateCompShareInstance", 226645, "Service unavailable")
                return FakeInstance()

        manager = Manager()
        provider = YouyunDiffusersProvider(manager, ssh_factory=FakeSSH, poll_interval=0, startup_timeout=1)

        async def scenario() -> None:
            outcome = await provider.start(
                "igt-fallback",
                {"resource": {"instanceMode": "pod", "fallbackEligible": True, "region": "cn-wlcb", "zone": "cn-wlcb-01", "gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"},
            )
            self.assertEqual(outcome["status"], "completed")

        asyncio.run(scenario())
        self.assertEqual([call["instance_mode"] for call in manager.calls], ["pod", "uhost"])
        self.assertEqual([call["zone"] for call in manager.calls], ["cn-wlcb-01", "cn-wlcb-02"])
        self.assertEqual(manager.cleaned, ["pod-image-leak", "uhost-img-1"])

    def test_capacity_inventory_failure_falls_back_to_uhost(self) -> None:
        class Manager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.client = type("Client", (), {"describe_support_zones": lambda _self, region=None: [{"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False}]})()
                self.calls: list[dict] = []

            def create_one_shot(self, task_id, **kwargs):
                self.calls.append(dict(kwargs))
                if len(self.calls) == 1:
                    callback = kwargs.get("on_created")
                    if callable(callback):
                        callback("pod-image-inventory-failure")
                    raise YouyunApiError(
                        "CheckCompShareResourceCapacity",
                        None,
                        "GPU 5090 在 cn-wlcb-01 无可用规格库存。",
                    )
                return FakeInstance()

        manager = Manager()
        provider = YouyunDiffusersProvider(manager, ssh_factory=FakeSSH, poll_interval=0, startup_timeout=1)

        async def scenario() -> None:
            outcome = await provider.start(
                "igt-inventory-fallback",
                {"resource": {"instanceMode": "pod", "fallbackEligible": True, "region": "cn-wlcb", "zone": "cn-wlcb-01", "gpuType": "5090", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"},
            )
            self.assertEqual(outcome["status"], "completed")

        asyncio.run(scenario())
        self.assertEqual([call["instance_mode"] for call in manager.calls], ["pod", "uhost"])
        self.assertEqual([call["zone"] for call in manager.calls], ["cn-wlcb-01", "cn-wlcb-02"])
        self.assertEqual(manager.cleaned, ["pod-image-inventory-failure", "uhost-img-1"])

    def test_adapter_upload_skips_identical_remote_files(self) -> None:
        digest = hashlib.sha256(b"weights").hexdigest()

        class SSH:
            def __init__(self) -> None:
                self.uploads: list[str] = []

            def run(self, command, timeout=120, check=True):
                return (0, f"{digest}  /remote/adapter/adapter_model.safetensors\n") if "sha256sum" in command else (0, "")

            def put_file(self, local, remote):
                self.uploads.append(remote)

        provider = YouyunDiffusersProvider(FakeManager())
        ssh = SSH()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "adapter_model.safetensors").write_bytes(b"weights")
            provider._upload_adapter(ssh, root, "/remote/adapter")  # type: ignore[attr-defined]
        self.assertEqual(ssh.uploads, [])

    def test_connect_retries_until_ssh_service_is_ready(self) -> None:
        FlakyConnectSSH.attempts = 0
        instances: list[FlakyConnectSSH] = []

        def factory():
            ssh = FlakyConnectSSH()
            instances.append(ssh)
            return ssh

        provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=factory)
        with patch.dict(
            "os.environ",
            {"YOUYUN_SSH_READY_TIMEOUT_SECONDS": "3", "YOUYUN_SSH_CONNECT_RETRY_DELAY_SECONDS": "1"},
            clear=False,
        ):
            ssh = provider._connect(FakeInstance())
        self.assertIs(instances[-1], ssh)
        self.assertEqual(FlakyConnectSSH.attempts, 2)
        self.assertTrue(instances[0].closed)

    def test_provision_service_reports_progress_stages(self) -> None:
        provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=lambda: FakeSSH())
        provider._prepare_runtime = lambda ssh, allow_install: "/usr/bin/python3"  # type: ignore[method-assign]
        provider._resolve_remote_model = lambda ssh, repo_id: "/models/acme/sdxl"  # type: ignore[method-assign]
        events: list[tuple[str, int]] = []

        def report_progress(stage: str, progress: int, message=None) -> None:
            events.append((stage, progress))

        result = asyncio.run(
            provider.provision_service(
                "igs-progress",
                {"resource": {"instanceMode": "uhost"}, "modelRepoId": "acme/sdxl", "adapterPath": ""},
                report_progress=report_progress,
            )
        )
        stages = [stage for stage, _ in events]
        self.assertEqual(stages[0], "creating_instance")
        self.assertIn("instance_running", stages)
        self.assertIn("ssh_connected", stages)
        self.assertIn("preparing_runtime", stages)
        self.assertIn("resolving_model", stages)
        self.assertNotIn("uploading_adapter", stages)
        self.assertEqual(stages[-1], "finalizing")
        self.assertEqual(events[-1][1], 90)
        self.assertEqual(result["handle"].instance_id, "uhost-img-1")

    def test_model_download_uses_only_fp16_sdxl_weights(self) -> None:
        script = YouyunDiffusersProvider._model_download_script()
        self.assertIn("huggingface_hub", script)
        self.assertIn("snapshot_download", script)
        for relative_path in (
            "text_encoder/model.fp16.safetensors",
            "text_encoder_2/model.fp16.safetensors",
            "unet/diffusion_pytorch_model.fp16.safetensors",
            "vae/diffusion_pytorch_model.fp16.safetensors",
        ):
            self.assertIn(relative_path, script)
        for relative_path in (
            "text_encoder/model.safetensors",
            "text_encoder_2/model.safetensors",
            "unet/diffusion_pytorch_model.safetensors",
            "vae/diffusion_pytorch_model.safetensors",
        ):
            self.assertNotIn(relative_path, script)

    def test_worker_loads_fp16_model_variant(self) -> None:
        script = YouyunDiffusersProvider._worker_script()
        self.assertIn('variant=config.get("variant")', script)

    def test_runtime_install_uses_torch_capable_interpreter(self) -> None:
        ssh = TorchOnlyRuntimeSSH()
        provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=lambda: ssh, remote_python="python3")
        with patch.dict("os.environ", {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            selected = provider._prepare_runtime(ssh)
        self.assertEqual(selected, "/usr/local/miniconda3/envs/py312/bin/python")
        self.assertTrue(any("/usr/local/miniconda3/envs/py312/bin/python -m pip install" in command for command in ssh.commands))
        self.assertTrue(any(" peft" in command for command in ssh.commands if "pip install" in command))

    def test_start_prefers_remote_model_and_downloads_results_then_cleans(self) -> None:
        async def scenario() -> None:
            manager = FakeManager()
            ssh = FakeSSH()
            with tempfile.TemporaryDirectory() as directory:
                provider = YouyunDiffusersProvider(
                    manager,
                    ssh_factory=lambda: ssh,
                    remote_root="/root/cppt/image-generation",
                    poll_interval=0,
                    startup_timeout=1,
                )
                outcome = await provider.start(
                    "igt-1",
                    {
                        "resource": {"gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "gpuType": "A10", "imageId": "diffusers"},
                        "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0",
                        "prompt": "a cat",
                        "negativePrompt": "blurry",
                        "width": 512,
                        "height": 512,
                        "steps": 20,
                        "cfg": 7,
                        "seed": 1,
                        "sampler": "euler",
                        "batchSize": 1,
                    },
                )
                self.assertEqual(outcome["status"], "completed")
                self.assertEqual(manager.created[0]["image_id"], "diffusers")
                self.assertEqual(manager.cleaned, ["uhost-img-1"])
                self.assertTrue(any("diffusers_worker.py" in path for path in ssh.files))
                self.assertTrue(any("--status-path" in command for command in ssh.commands))
                config = next(value for path, value in ssh.files.items() if path.endswith("task.json"))
                self.assertIn('"variant": "fp16"', config)
                self.assertTrue((Path(directory) / "0.png").exists() or outcome["results"][0].get("fileName") == "0.png")

        asyncio.run(scenario())

    def test_start_emits_runtime_and_model_progress_logs(self) -> None:
        async def scenario() -> None:
            logs: list[str] = []
            provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=FakeSSH, poll_interval=0, startup_timeout=1)
            outcome = await provider.start(
                "igt-progress-logs",
                {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"},
                on_log=logs.append,
            )
            self.assertEqual(outcome["status"], "completed")
            self.assertIn("SSH 连接已建立。", logs)
            self.assertIn("正在检查优云基座模型。", logs)
            self.assertIn("Diffusers worker 已启动，开始生成图片。", logs)

        asyncio.run(scenario())

    def test_start_uploads_server_resolved_adapter(self) -> None:
        async def scenario() -> None:
            manager = FakeManager()
            ssh = FakeSSH()
            with tempfile.TemporaryDirectory() as directory:
                adapter = Path(directory) / "adapter"
                adapter.mkdir()
                (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
                (adapter / "adapter_model.safetensors").write_text("weights", encoding="utf-8")
                provider = YouyunDiffusersProvider(manager, ssh_factory=lambda: ssh, poll_interval=0, startup_timeout=1)
                outcome = await provider.start("igt-adapter", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "adapterPath": str(adapter), "prompt": "cat", "width": 512, "height": 512, "steps": 1, "cfg": 1, "seed": 1, "sampler": "euler", "batchSize": 1})
                self.assertEqual(outcome["status"], "completed")
                self.assertTrue(any(path.endswith("adapter_model.safetensors") for path in ssh.files))
                config = next(value for path, value in ssh.files.items() if path.endswith("task.json"))
                self.assertIn("/adapter", config)
                self.assertNotIn(str(adapter), config)

        asyncio.run(scenario())

    def test_cleanup_failure_is_returned_without_hiding_generation_error(self) -> None:
        async def scenario() -> None:
            manager = FakeManager({"terminated": False, "error": "欠费无法删除"})
            ssh = FakeSSH()
            ssh.status_reads = 1
            original_run = ssh.run

            def broken_run(command, timeout=120, check=True):
                if "status.json" in command and "cat" in command:
                    return 0, '{"state":"failed","error":"CUDA unavailable"}\n'
                return original_run(command, timeout, check)

            ssh.run = broken_run  # type: ignore[method-assign]
            provider = YouyunDiffusersProvider(manager, ssh_factory=lambda: ssh, poll_interval=0, startup_timeout=1)
            outcome = await provider.start("igt-2", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat", "width": 512, "height": 512, "steps": 1, "cfg": 1, "seed": 1, "sampler": "euler", "batchSize": 1})
            self.assertEqual(outcome["status"], "failed")
            self.assertIn("CUDA unavailable", outcome["error"])
            self.assertEqual(outcome["failureCode"], "generation_failed")
            self.assertEqual(outcome["cleanupState"], "failed")
            self.assertEqual(outcome["cleanupError"], "欠费无法删除")

        asyncio.run(scenario())

    def test_model_download_fallback_can_be_disabled(self) -> None:
        manager = FakeManager()
        ssh = FakeSSH()
        original_run = ssh.run

        def missing_model(command, timeout=120, check=True):
            if "base-model-path" in command:
                return 0, "base-model-error:no matching complete model\n"
            return original_run(command, timeout, check)

        ssh.run = missing_model  # type: ignore[method-assign]
        provider = YouyunDiffusersProvider(manager, ssh_factory=lambda: ssh)
        with patch.dict("os.environ", {"YOUYUN_ALLOW_MODEL_DOWNLOAD": "0"}, clear=False):
            with self.assertRaises(RuntimeError):
                provider._resolve_remote_model(ssh, "acme/flux")

    def test_rejects_unsafe_server_repo_id(self) -> None:
        provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=FakeSSH)
        with self.assertRaises(ValueError):
            provider._model_resolution_command("../../unsafe")

    def test_early_process_exit_is_classified_as_generation_failure(self) -> None:
        async def scenario() -> None:
            provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=EarlyExitSSH, poll_interval=0, startup_timeout=1)
            outcome = await provider.start("igt-early-exit", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"})
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["failureCode"], "generation_failed")
            self.assertNotIn("kill -0", str(outcome))

        asyncio.run(scenario())

    def test_invalid_result_download_is_classified_separately(self) -> None:
        async def scenario() -> None:
            provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=InvalidDownloadSSH, poll_interval=0, startup_timeout=1)
            outcome = await provider.start("igt-download", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"})
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["failureCode"], "result_download_failed")

        asyncio.run(scenario())

    def test_runtime_and_model_failures_have_stable_codes(self) -> None:
        async def scenario() -> None:
            runtime_provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=RuntimeUnavailableSSH, poll_interval=0, startup_timeout=1)
            runtime_outcome = await runtime_provider.start("igt-runtime", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"})
            self.assertEqual(runtime_outcome["failureCode"], "runtime_unavailable")

            model_provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=ModelUnavailableSSH, poll_interval=0, startup_timeout=1)
            with patch.dict("os.environ", {"YOUYUN_ALLOW_MODEL_DOWNLOAD": "0"}, clear=False):
                model_outcome = await model_provider.start("igt-model", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"})
            self.assertEqual(model_outcome["failureCode"], "model_unavailable")

        asyncio.run(scenario())

    def test_adapter_failure_has_stable_code(self) -> None:
        async def scenario() -> None:
            provider = YouyunDiffusersProvider(FakeManager(), ssh_factory=FakeSSH, poll_interval=0, startup_timeout=1)
            outcome = await provider.start("igt-adapter-missing", {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "adapterPath": "C:/missing-adapter", "prompt": "cat"})
            self.assertEqual(outcome["failureCode"], "adapter_unavailable")

        asyncio.run(scenario())

    def test_instance_start_failure_is_safe_and_classified(self) -> None:
        async def scenario() -> None:
            provider = YouyunDiffusersProvider(InstanceStartFailureManager(), ssh_factory=FakeSSH, poll_interval=0, startup_timeout=1)
            stages: list[tuple[str, str, dict]] = []
            logs: list[str] = []
            outcome = await provider.start(
                "igt-create-failure",
                {"resource": {"gpuType": "A10", "imageId": "diffusers"}, "modelRepoId": "acme/flux", "prompt": "cat"},
                on_stage=lambda stage, message, details: stages.append((stage, message, dict(details))),
                on_log=logs.append,
            )
            self.assertEqual(outcome["failureCode"], "instance_start_failed")
            self.assertNotIn("super-secret", str(outcome))
            self.assertNotIn("command=", str(outcome))
            self.assertNotIn("super-secret", str(stages))
            self.assertNotIn("command=", str(logs))

        asyncio.run(scenario())

    def test_safe_detail_redacts_common_credential_formats(self) -> None:
        for detail in (
            "password: super-secret",
            "token: super-secret",
            "AKIA1234567890ABCDEF",
            "ghp_abcdefghijklmnopqrstuvwxyz",
            "python3 -c import torch",
        ):
            self.assertEqual(YouyunDiffusersProvider._safe_detail(detail), "远端文生图执行失败。")


if __name__ == "__main__":
    unittest.main()
