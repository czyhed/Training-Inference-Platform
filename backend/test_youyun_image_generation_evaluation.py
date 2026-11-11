from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.providers.youyun.image_generation_evaluation import YouyunImageGenerationEvaluationExecutor


class FakeInstance:
    uhost_id = "uhost-eval-1"
    ip = "10.0.0.9"
    ssh_port = 22
    ssh_user = "root"


class FakeManager:
    def __init__(self, cleanup: dict | None = None) -> None:
        self.created = 0
        self.cleaned: list[str] = []
        self.cleanup = cleanup or {"terminated": True, "error": None}

    def create_one_shot(self, task_id, **kwargs):
        self.created += 1
        return FakeInstance()

    def ssh_credentials(self, instance):
        return {"host": instance.ip, "port": instance.ssh_port, "user": instance.ssh_user, "password": "secret"}

    def cleanup_after_task(self, instance_id, stop_first=False):
        self.cleaned.append(instance_id)
        return self.cleanup


class FakeSSH:
    def __init__(self, *, status: str = "completed") -> None:
        self.status = status
        self.files: dict[str, str] = {}
        self.commands: list[str] = []
        self.closed = False

    def connect(self, *args, **kwargs):
        return None

    def close(self):
        self.closed = True

    def mkdirs(self, path):
        self.commands.append(f"mkdirs {path}")

    def put_text(self, text, path):
        self.files[path] = text

    def put_file(self, local, remote):
        self.files[remote] = str(local)

    def get_file(self, remote, local):
        Path(local).parent.mkdir(parents=True, exist_ok=True)
        Path(local).write_bytes(b"png")

    def run(self, command, timeout=120, check=True):
        self.commands.append(command)
        if "import torch, diffusers, transformers, PIL" in command:
            return 0, "/opt/conda/bin/python\n"
        if "base-model-path" in command:
            return 0, "base-model-path:/models/stabilityai___stable-diffusion-xl-base-1.0\n"
        if "cat" in command and "status.json" in command:
            return 0, '{"state":"completed","progress":100,"results":[{"sampleId":"sample-1","state":"completed","fileName":"sample-1.png","latencyMs":12,"clipTextImage":0.81,"clipReferenceImage":0.72},{"sampleId":"sample-2","state":"completed","fileName":"sample-2.png","latencyMs":14,"clipTextImage":0.83,"clipReferenceImage":0.74}]}\n'
        return 0, ""


class YouyunImageGenerationEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def task(self, root: Path) -> dict:
        reference = root / "reference.png"
        reference.write_bytes(b"png")
        return {
            "id": "ige-test",
            "modelRepoId": "stabilityai/stable-diffusion-xl-base-1.0",
            "adapterPath": "",
            "resource": {"gpuCount": 1, "cpuCores": 8, "memoryGb": 32, "gpuType": "A10", "imageId": "diffusers"},
            "width": 512,
            "height": 512,
            "steps": 4,
            "cfg": 7,
            "seed": 1,
            "sampler": "euler",
            "resultRoot": str(root / "results"),
            "samples": [
                {"id": "sample-1", "caption": "a cat", "referencePath": str(reference)},
                {"id": "sample-2", "caption": "a dog", "referencePath": str(reference)},
            ],
        }

    async def test_one_instance_processes_complete_dataset_and_downloads_results(self):
        manager = FakeManager()
        ssh = FakeSSH()
        with tempfile.TemporaryDirectory() as directory:
            provider = YouyunImageGenerationEvaluationExecutor(manager, ssh_factory=lambda: ssh, poll_interval=0, timeout=2)
            events = []
            result = await provider.run(self.task(Path(directory)), on_event=lambda *event: events.append(event))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["summary"]["completedSamples"], 2)
            self.assertEqual(manager.created, 1)
            self.assertEqual(manager.cleaned, ["uhost-eval-1"])
            self.assertTrue((Path(directory) / "results" / "generated" / "sample-1.png").is_file())
            self.assertTrue(any(path.endswith("task.json") for path in ssh.files))
            self.assertTrue(any((details or {}).get("remoteHandle", {}).get("instanceId") == "uhost-eval-1" for _, _, details in events))

    async def test_dependency_install_is_explicit_and_uses_selected_python(self):
        class InstallSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                self.commands.append(command)
                if "pip install" in command:
                    return 0, "installed\n"
                if "import torch, diffusers, transformers, PIL" in command:
                    return 42, ""
                if "import torch'" in command:
                    return 0, "/usr/local/miniconda3/envs/py312/bin/python\n"
                return super().run(command, timeout, check)

        ssh = InstallSSH()
        provider = YouyunImageGenerationEvaluationExecutor(FakeManager(), ssh_factory=lambda: ssh, remote_python="python3")
        with patch.dict("os.environ", {"XUNTUI_REMOTE_IMAGE_EVAL_INSTALL": "1"}, clear=False):
            selected = provider._prepare_runtime(ssh)
        self.assertEqual(selected, "/usr/local/miniconda3/envs/py312/bin/python")
        self.assertTrue(any("/usr/local/miniconda3/envs/py312/bin/python -m pip install" in command for command in ssh.commands))

    async def test_cleanup_failure_is_reported_without_exposing_credentials(self):
        manager = FakeManager({"terminated": False, "error": "password=secret"})
        ssh = FakeSSH()
        with tempfile.TemporaryDirectory() as directory:
            provider = YouyunImageGenerationEvaluationExecutor(manager, ssh_factory=lambda: ssh, poll_interval=0, timeout=2)
            task = self.task(Path(directory))
            task["modelRepoId"] = "../../unsafe"
            result = await provider.run(task)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failureCode"], "model_unavailable")
            self.assertNotIn("password=", result["error"])
            self.assertEqual(result["cleanupState"], "failed")


if __name__ == "__main__":
    unittest.main()
