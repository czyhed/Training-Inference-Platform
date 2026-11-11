from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.providers.youyun.image_generation_training import YouyunImageGenerationTrainingExecutor
from backend.providers.youyun.models import YouyunApiError


class ProbeSSH:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command, timeout=120, check=True):
        self.commands.append(command)
        if "import torch, diffusers, peft" in command:
            return 42, ""
        if "import torch" in command:
            return 0, "/opt/conda/bin/python\n"
        return 0, "installed\n"


class YouyunImageGenerationTrainingTests(unittest.TestCase):
    def test_preflight_rejects_missing_materials_before_instance_creation(self) -> None:
        class Manager:
            def __init__(self) -> None:
                self.created = False

            def create_one_shot(self, *args, **kwargs):
                self.created = True
                raise AssertionError("预检失败时不应创建优云实例")

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                manager = Manager()
                result = await YouyunImageGenerationTrainingExecutor(manager).run(
                    {"id": "igt-preflight", "previewRoot": str(Path(directory) / "missing"), "resource": {}}
                )
                self.assertEqual(result["status"], "failed")
                self.assertIn("训练物化目录不存在", result["error"])
                self.assertFalse(manager.created)

        asyncio.run(scenario())

    def test_model_download_uses_only_fp16_sdxl_weights(self) -> None:
        script = YouyunImageGenerationTrainingExecutor._model_download_script()
        self.assertIn("import importlib.util", script)
        self.assertIn("import sys", script)
        self.assertIn("huggingface_hub", script)
        self.assertIn("resume_download", script)
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

    def test_remote_script_download_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD", None)
            self.assertFalse(YouyunImageGenerationTrainingExecutor.allow_remote_script_download())

    def test_script_setup_command_checks_preinstalled_script_without_download_fallback(self) -> None:
        with patch.dict(os.environ, {"XUNTUI_ALLOW_REMOTE_SCRIPT_DOWNLOAD": "0"}, clear=False):
            executor = YouyunImageGenerationTrainingExecutor(manager=None)
            self.assertFalse(executor.allow_remote_script_download())

    def test_failure_code_distinguishes_ssh_eof_from_generic_training(self) -> None:
        executor = YouyunImageGenerationTrainingExecutor(manager=None)
        self.assertEqual(
            executor.failure_code(EOFError(), "model_download"),
            "ssh_channel_disconnected",
        )
        self.assertEqual(
            executor.failure_code(RuntimeError("pip failed"), "dependency_install"),
            "dependency_install_failed",
        )
        self.assertEqual(
            executor.failure_code(RuntimeError("ssl handshake timeout"), "script_setup"),
            "training_script_download_failed",
        )

    def test_failure_code_distinguishes_youyun_capacity_from_generic_training(self) -> None:
        executor = YouyunImageGenerationTrainingExecutor(manager=None)
        error = YouyunApiError(
            "CheckCompShareResourceCapacity",
            None,
            "GPU 4090 在 cn-wlcb-01 无可用规格库存。",
        )
        self.assertEqual(
            executor.failure_code(error, "instance_provision"),
            "youyun_create_no_capacity",
        )

    def test_instance_provision_passes_selected_region_zone_and_charge_type(self) -> None:
        class Instance:
            uhost_id = "uhost-region"

        class Manager:
            def __init__(self) -> None:
                self.kwargs = None

            def create_one_shot(self, *args, **kwargs):
                self.kwargs = kwargs
                return Instance()

            def cleanup_after_task(self, *_args, **_kwargs):
                return {"terminated": True, "error": None}

        class Executor(YouyunImageGenerationTrainingExecutor):
            def _connect(self, _instance):
                raise RuntimeError("stop after provisioning")

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "dataset").mkdir()
                (root / "train.jsonl").write_text('{"prompt":"test"}\n', encoding="utf-8")
                (root / "train_config.json").write_text(json.dumps({"model_path": "unused"}), encoding="utf-8")
                manager = Manager()
                result = await Executor(manager).run(
                    {
                        "id": "igt-region",
                        "previewRoot": str(root),
                        "resource": {
                            "region": "cn-bj2",
                            "zone": "cn-bj2-03",
                            "chargeType": "Postpay",
                            "gpuType": "4090",
                            "gpuCount": 1,
                            "cpuCores": 14,
                            "memoryGb": 32,
                            "imageId": "img-1",
                        },
                    }
                )
                self.assertEqual(result["status"], "failed")
                self.assertIsNotNone(manager.kwargs)
                self.assertEqual(manager.kwargs["region"], "cn-bj2")
                self.assertEqual(manager.kwargs["zone"], "cn-bj2-03")
                self.assertEqual(manager.kwargs["charge_type"], "Postpay")

        asyncio.run(scenario())

    def test_install_fallback_uses_torch_capable_interpreter(self) -> None:
        ssh = ProbeSSH()
        executor = YouyunImageGenerationTrainingExecutor(manager=None, remote_python="/missing/python")
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            selected = executor._probe_runtime(ssh, executor.remote_python)
        self.assertEqual(selected, "/opt/conda/bin/python")
        self.assertIn("/opt/conda/bin/python -m pip install", ssh.commands[-1])
        self.assertIn("--retries 3 --timeout 60", ssh.commands[-1])

    def test_install_fallback_writes_remote_ready_sentinel(self) -> None:
        ssh = ProbeSSH()
        executor = YouyunImageGenerationTrainingExecutor(manager=None, remote_python="/missing/python")
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1", "XUNTUI_REMOTE_DIFFUSERS_SENTINEL": "/root/cppt/.cache/diffusers-ready"}, clear=False):
            executor._probe_runtime(ssh, executor.remote_python)
        self.assertIn("diffusers-ready", ssh.commands[-1])
        self.assertIn("touch", ssh.commands[-1])

    def test_install_fallback_does_not_select_torch_only_python_without_pip(self) -> None:
        class SSH:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def run(self, command, timeout=120, check=True, **kwargs):
                self.commands.append(command)
                if "import torch, diffusers, peft" in command:
                    return 42, ""
                if "import torch" in command and "pip" in command:
                    return 0, "/opt/conda/bin/python\n"
                if "import torch" in command:
                    return 0, "/usr/bin/python3\n"
                return 0, "installed\n"

        ssh = SSH()
        executor = YouyunImageGenerationTrainingExecutor(manager=None, remote_python="python3")
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            selected = executor._probe_runtime(ssh, executor.remote_python)
        self.assertEqual(selected, "/opt/conda/bin/python")
        self.assertIn("-m pip --version", ssh.commands[1])

    def test_install_fallback_reports_missing_pip_interpreter_before_install(self) -> None:
        class SSH:
            def __init__(self) -> None:
                self.commands: list[str] = []

            def run(self, command, timeout=120, check=True, **kwargs):
                self.commands.append(command)
                if "import torch, diffusers, peft" in command:
                    return 42, ""
                if "import torch" in command:
                    return 42, ""
                return 0, "installed\n"

        ssh = SSH()
        executor = YouyunImageGenerationTrainingExecutor(manager=None, remote_python="python3")
        with patch.dict(os.environ, {"XUNTUI_REMOTE_DIFFUSERS_INSTALL": "1"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "同时具备 Torch 和 pip"):
                executor._probe_runtime(ssh, executor.remote_python)
        self.assertFalse(any("pip install" in command for command in ssh.commands))

    def test_connect_retries_transient_ssh_banner(self) -> None:
        class Instance:
            uhost_id = "uhost-1"
            state = "Running"

        class Manager:
            def ssh_credentials(self, _instance):
                return {"host": "1.2.3.4", "port": 22, "user": "root", "password": "secret"}

            def get(self, _uhost_id):
                return Instance()

        class SSH:
            attempts = 0

            def connect(self, *_args):
                type(self).attempts += 1
                if self.attempts < 2:
                    raise ConnectionError("Error reading SSH protocol banner")

            def close(self):
                return None

        executor = YouyunImageGenerationTrainingExecutor(Manager(), ssh_factory=SSH, poll_interval=0)
        with patch.dict(os.environ, {"YOUYUN_SSH_READY_TIMEOUT_SECONDS": "10", "YOUYUN_SSH_CONNECT_RETRY_DELAY_SECONDS": "1"}, clear=False):
            self.assertIsInstance(executor._connect(Instance()), SSH)
        self.assertEqual(SSH.attempts, 2)

    def test_executor_reports_instance_handle_before_ssh_setup(self) -> None:
        class Instance:
            uhost_id = "uhost-1"
            gpu_type = "4090"
            gpu = 1
            zone = "cn-wlcb-01"

        class Manager:
            def create_one_shot(self, *args, **kwargs):
                return Instance()

            def cleanup_after_task(self, uhost_id, stop_first=True, **_kwargs):
                return {"terminated": True, "error": None}

        class Executor(YouyunImageGenerationTrainingExecutor):
            def _connect(self, instance):
                raise RuntimeError("stop after instance event")

        events = []
        result = asyncio.run(Executor(Manager()).run({"id": "igt-1", "resource": {}}, on_event=events.append))
        self.assertEqual(result["status"], "failed")
        handle_event = next(event for event in events if (event.payload or {}).get("remoteHandle"))
        self.assertEqual(handle_event.payload["remoteHandle"]["instanceId"], "uhost-1")

    def test_executor_persists_cleanup_failure_in_result(self) -> None:
        class Instance:
            uhost_id = "uhost-1"
            gpu_type = "4090"
            gpu = 1
            zone = "cn-wlcb-01"

        class Manager:
            def create_one_shot(self, *args, **kwargs):
                return Instance()

            def cleanup_after_task(self, uhost_id, stop_first=True, **_kwargs):
                return {"terminated": False, "error": "delete timed out"}

        class Executor(YouyunImageGenerationTrainingExecutor):
            def _connect(self, instance):
                raise RuntimeError("stop after instance event")

        result = asyncio.run(Executor(Manager()).run({"id": "igt-1", "resource": {}}))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["cleanupState"], "failed")
        self.assertEqual(result["cleanupError"], "delete timed out")

    def test_executor_emits_actual_remote_preparation_phases(self) -> None:
        source = Path("backend/providers/youyun/image_generation_training.py").read_text(encoding="utf-8")
        expected = (
            ("正在检测远端 Torch、pip 与 Diffusers 环境。", 'phase = "dependency_install"'),
            ("正在上传训练数据。", 'phase = "upload"'),
            ("正在下载 SDXL 基座模型。", 'phase = "model_download"'),
            ("SDXL 基座模型准备完成。", 'phase = "model_resolution"'),
        )
        for message, assignment in expected:
            start = source.index(message)
            statement = source[start:source.index(")", start) + 1]
            self.assertIn("phase=phase", statement, message)
            self.assertIn(assignment, source[:start], message)

    def test_worker_updates_emit_new_logs_and_replace_changed_step_metric(self) -> None:
        executor = YouyunImageGenerationTrainingExecutor(manager=None)
        state = {"logs": set(), "metrics": {}}
        events = []
        first = (
            "Preparing dataset\n"
            "Steps:  10%|#         | 1/10 [00:01<00:09, 1.00it/s, lr=0.0001, step_loss=0.5]\r"
        )
        updated = first.replace("step_loss=0.5", "step_loss=0.4")

        asyncio.run(executor._emit_worker_updates(first, state, events.append))
        asyncio.run(executor._emit_worker_updates(first, state, events.append))
        asyncio.run(executor._emit_worker_updates(updated, state, events.append))

        self.assertEqual([event.type for event in events], ["log", "metric", "metric"])
        self.assertEqual(events[0].message, "Preparing dataset")
        self.assertEqual(events[-1].payload["loss"], 0.4)
        self.assertEqual(events[-1].payload["learning_rate"], 0.0001)

    def test_cleanup_remote_discovers_task_instance_by_name(self) -> None:
        class Instance:
            uhost_id = "uhost-1"
            name = "cppt-img-train-igt-orphan"

        class Client:
            def describe_instances(self):
                return [Instance()]

        class Manager:
            client = Client()

            def cleanup_after_task(self, uhost_id, stop_first=True, **_kwargs):
                return {"terminated": uhost_id == "uhost-1", "error": None}

        result = asyncio.run(YouyunImageGenerationTrainingExecutor(Manager()).cleanup_remote({"id": "igt-orphan"}))
        self.assertEqual(result["cleanupState"], "completed")


if __name__ == "__main__":
    unittest.main()
