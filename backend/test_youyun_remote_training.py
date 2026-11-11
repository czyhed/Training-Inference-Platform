"""YouyunTrainingRunner 端到端单元测试：伪造 SSH/实例管理/store，覆盖成功、失败、停止路径。"""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.providers.youyun.models import YouyunApiError, YouyunInstance
from backend.providers.youyun.instance_manager import YouyunProvisionCancelled
from backend.providers.youyun.remote_training import _INSTALL_COMMAND, YouyunTrainingRunner, _failure_code
from backend.providers.youyun.image_generation_training import YouyunImageGenerationTrainingExecutor

LOG_LINES = [
    "***** Running training *****",
    "Total optimization steps 20",
    "{'loss': 2.31, 'learning_rate': 5e-05, 'epoch': 0.05}",
    "{'loss': 1.82, 'learning_rate': 5e-05, 'epoch': 0.10}",
]


class FakeSSH:
    def __init__(self, *, install_polls: int = 1, install_fail: bool = False, install_process_dead: bool = False) -> None:
        self.install_polls = install_polls
        self.install_fail = install_fail
        self.install_process_dead = install_process_dead
        self.install_poll_count = 0
        self.train_poll_count = 0
        self.uploaded: dict[str, str] = {}
        self.mkdir_calls: list[str] = []
        self.get_tree_calls: list[str] = []
        self.commands: list[str] = []

    def log_connection(self):
        from backend.test_remote_log_follow import MemoryLogSSH
        return MemoryLogSSH(lambda: ("\n".join(LOG_LINES) + "\ntrain finished\n").encode())

    def connect(self, host, port, user, password) -> None:
        return None

    def close(self) -> None:
        return None

    def run(self, command, timeout=120, check=True):
        self.commands.append(command)
        if "base-model-path" in command:
            return 0, "base-model-path:/root/models/Qwen2.5-VL-3B-Instruct\n"
        if "python-path" in command and "env-install.done" not in command:
            return 0, "python-path-ready\n"
        if "yaml.safe_load" in command and "model_name_or_path" in command:
            return 0, ""
        if "nvidia-smi" in command:
            return 0, "NVIDIA GeForce RTX 4090, 24564 MiB\n"
        if "env-install.done" in command and "cat" in command:
            self.install_poll_count += 1
            if self.install_process_dead:
                return 0, "install-started\ninstall-dead\n---\nINSTALL_PROCESS_DEAD\n"
            if self.install_poll_count <= self.install_polls:
                return 0, "---\nstill installing\n"
            marker = "fail" if self.install_fail else "ok"
            path_marker = "" if getattr(self, "install_python_path_missing", False) else "python-path-ready\n"
            return 0, f"{marker}\n{path_marker}---\nlast install line\n"
        if "nohup" in command and "env-install" in command:
            return 0, ""
        if "MODEL_MISS" in command:
            return 0, "MODEL_READY:/root/cppt/modelscope-cache/models/demo\n"
        if "train.yaml" in command and "nohup" in command:
            return 0, ""
        if command.startswith("echo code:"):
            self.train_poll_count += 1
            if self.train_poll_count <= len(LOG_LINES):
                line = LOG_LINES[self.train_poll_count - 1]
                return 0, f"code:none\n{line}\n"
            return 0, "code:0\ntrain finished\n"
        return 0, ""

    def mkdirs(self, remote_dir: str) -> None:
        self.mkdir_calls.append(remote_dir)

    def put_file(self, local_path, remote_path) -> None:
        self.uploaded[remote_path] = Path(local_path).read_text(encoding="utf-8")

    def put_text(self, text: str, remote_path: str) -> None:
        self.uploaded[remote_path] = text

    def get_tree(self, remote_dir: str, local_dir) -> list[str]:
        self.get_tree_calls.append(remote_dir)
        output = Path(local_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "adapter_config.json").write_text(
            '{"base_model_name_or_path": "/root/cppt/modelscope-cache/models/demo"}',
            encoding="utf-8",
        )
        (output / "trainer_state.json").write_text(json.dumps({"log_history": []}), encoding="utf-8")
        return [str(output / "adapter_config.json")]

    def get_file(self, remote_path: str, local_path) -> None:
        self.commands.append(f"get_file:{remote_path}")
        Path(local_path).write_text(
            '{"max_position_embeddings": 32768}', encoding="utf-8"
        )


class FakeManager:
    def __init__(self) -> None:
        self.cleaned: list[str] = []
        self.cleanup_error: str | None = None

    def create_one_shot(self, task_id, **kwargs):
        return YouyunInstance(
            uhost_id="uhost-9", name="cppt-t", state="Running", region="cn-wlcb", zone="cn-wlcb-01",
            gpu=1, gpu_type="4090", cpu=16, memory_mb=65536, ip="1.2.3.4", ssh_port=60022,
            ssh_user="root", password_b64="cGFzcw==", image_id="img", os_type="Linux",
            charge_type="Postpay", instance_type="UHost", raw={},
        )

    def ssh_credentials(self, instance):
        return {"host": instance.ip, "port": instance.ssh_port, "user": instance.ssh_user, "password": "pass"}

    def cleanup_after_task(self, uhost_id, stop_first=True):
        self.cleaned.append(uhost_id)
        return {"terminated": not self.cleanup_error, "error": self.cleanup_error}


class FakeSink:
    def __init__(self, cancel_after_logs: int | None = None) -> None:
        self.logs: list[str] = []
        self.metrics: list[dict] = []
        self.updates: list[dict] = []
        self.artifacts: list[dict] = []
        self._cancel_after = cancel_after_logs
        self._log_count = 0

    def append_log(self, task_id, message) -> None:
        self.logs.append(message)
        self._log_count += 1

    def append_metric(self, task_id, **kw) -> None:
        self.metrics.append(kw)

    def update(self, task_id, **kw) -> None:
        self.updates.append(kw)
        if "artifacts" in kw:
            self.artifacts = kw["artifacts"]

    def get(self, task_id):
        return {"artifacts": self.artifacts}

    def is_cancel_requested(self, task_id) -> bool:
        return self._cancel_after is not None and self._log_count >= self._cancel_after

    def metrics_(self, task_id) -> list[dict]:
        return self.metrics


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class RunnerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mirror = Path(self.tmp.name)
        self.ssh = FakeSSH()
        self.manager = FakeManager()

    def make_runner(self, sink: FakeSink, **runner_options) -> YouyunTrainingRunner:
        async def prepare_local(task):
            workdir = self.mirror / task["taskId"]
            (workdir / "dataset").mkdir(parents=True, exist_ok=True)
            (workdir / "dataset" / "train.jsonl").write_text("{}\n", encoding="utf-8")
            (workdir / "dataset" / "dataset_info.json").write_text("{}", encoding="utf-8")
            (workdir / "train.yaml").write_text("dataset_dir: /local/dataset\noutput_dir: /local/output\n", encoding="utf-8")
            return SimpleNamespace(workdir=str(workdir), config_path=str(workdir / "train.yaml"), prepared=True)

        async def collect_local(task):
            workdir = self.mirror / (task.get("taskId") or task.get("id"))
            adapter = workdir / "output" / "adapter_config.json"
            assert adapter.is_file(), "collect 前产物应已下载到本地镜像"
            return [{"type": "adapter", "name": "final", "variant": "final", "path": str(adapter)}]

        runner_options.setdefault("youyun_base_model_root", "/root/models")
        return YouyunTrainingRunner(
            manager=self.manager,
            sink=sink,
            ssh_factory=lambda: self.ssh,
            mirror_root=self.mirror,
            prepare_local=prepare_local,
            collect_local=collect_local,
            sleep=lambda s: None,
            clock=_FakeClock(),
            **runner_options,
        )

    def test_remote_install_pins_torch_below_2_9_for_visual_training(self) -> None:
        self.assertIn("torch==2.8.0", _INSTALL_COMMAND)

    def test_remote_install_removes_optional_deepspeed_without_cuda_toolkit(self) -> None:
        """Single-card images without nvcc must not fail in accelerate's optional import."""
        self.assertIn("command -v nvcc", _INSTALL_COMMAND)
        self.assertIn("pip uninstall -y deepspeed", _INSTALL_COMMAND)
        self.assertIn("deepspeed detected without nvcc", _INSTALL_COMMAND)

    def test_image_generation_model_download_filters_large_unused_formats(self) -> None:
        script = YouyunImageGenerationTrainingExecutor._model_download_script()
        self.assertIn("allow_file_pattern=allow", script)
        self.assertIn("ignore_file_pattern=ignore", script)
        self.assertIn("unet/diffusion_pytorch_model.fp16.safetensors", script)
        self.assertIn("vae/diffusion_pytorch_model.fp16.safetensors", script)
        self.assertIn("text_encoder_2/model.fp16.safetensors", script)
        self.assertNotIn("'*.safetensors'", script)
        self.assertNotIn("sd_xl_base_1.0.safetensors", script)
        self.assertIn("拒绝使用未认证 Hugging Face 下载", script)

    def test_remote_install_uses_isolated_python_environment(self) -> None:
        self.assertIn("-m venv", _INSTALL_COMMAND)
        self.assertIn("--ignore-installed --no-deps 'torch==2.8.0'", _INSTALL_COMMAND)

    def test_remote_install_reuses_compatible_youyun_environment_before_fallback(self) -> None:
        self.assertIn("REUSE_PY", _INSTALL_COMMAND)
        self.assertIn("CORE_PY", _INSTALL_COMMAND)
        self.assertIn("视觉训练环境探测通过", _INSTALL_COMMAND)
        self.assertIn("已复用优云 PyTorch 环境并补齐视觉训练依赖", _INSTALL_COMMAND)
        self.assertIn('if [ -n "$REUSE_PY" ]', _INSTALL_COMMAND)
        self.assertLess(_INSTALL_COMMAND.index("REUSE_PY"), _INSTALL_COMMAND.index("torch==2.8.0"))

    def test_remote_install_does_not_publish_shared_python_path_before_success(self) -> None:
        """An old installer must not overwrite the active interpreter marker."""
        self.assertNotIn('echo "$PY" > /root/cppt/python-path', _INSTALL_COMMAND)
        self.assertNotIn('echo "$PY" >/root/cppt/python-path', _INSTALL_COMMAND)

    def test_remote_install_resolves_factory_dependencies_with_torch_constraint(self) -> None:
        self.assertNotIn("--no-deps llamafactory", _INSTALL_COMMAND)
        self.assertIn("--constraint /root/cppt/torch-constraints.txt", _INSTALL_COMMAND)

    def test_remote_install_overlays_matching_torch_audio_and_vision_wheels(self) -> None:
        self.assertIn("torchvision==0.23.0", _INSTALL_COMMAND)
        self.assertIn("torchaudio==2.8.0", _INSTALL_COMMAND)

    def test_remote_torch_overlay_reuses_image_cuda_dependencies(self) -> None:
        self.assertIn(
            "--no-deps 'torch==2.8.0' 'torchvision==0.23.0' 'torchaudio==2.8.0'",
            _INSTALL_COMMAND,
        )
        self.assertIn("BASE_PREFIX", _INSTALL_COMMAND)

    def test_remote_dependency_resolution_keeps_torch_audio_versions_pinned(self) -> None:
        self.assertIn(
            "printf 'torch==2.8.0\\ntorchvision==0.23.0\\ntorchaudio==2.8.0\\n'",
            _INSTALL_COMMAND,
        )

    def test_remote_environment_does_not_inherit_incompatible_system_packages(self) -> None:
        self.assertNotIn("--system-site-packages", _INSTALL_COMMAND)
        self.assertIn("site-packages/nvidia", _INSTALL_COMMAND)
        self.assertIn("LD_LIBRARY_PATH", _INSTALL_COMMAND)

    def test_remote_training_reloads_isolated_cuda_library_path(self) -> None:
        self.assertIn("cuda-library-path", _INSTALL_COMMAND)

    def test_remote_install_publishes_cuda_path_only_after_generation_success(self) -> None:
        # The trainer reads this marker after the dependency script exits.  It
        # must be published atomically alongside python-path, otherwise a
        # stale or half-written CUDA path can make the next launch fail.
        asyncio.run(self.make_runner(FakeSink())._install_environment("cuda-path-publish", self.ssh))
        script = self.ssh.uploaded["/tmp/env-install.sh"]
        self.assertIn("cuda-library-path.tmp", script)
        self.assertIn("mv -f /root/cppt/cuda-library-path.tmp /root/cppt/cuda-library-path", script)

    def test_remote_model_resolution_requires_complete_model_in_youyun_root(self) -> None:
        runner = self.make_runner(FakeSink())
        command = runner._model_resolution_command(
            "Qwen/Qwen2.5-VL-3B-Instruct",
            "/root/models:/models",
        )
        self.assertIn("base-model-path", command)
        self.assertIn("base-model-error", command)
        self.assertIn("config.json", command)
        self.assertIn("tokenizer_config.json", command)
        self.assertNotIn("modelscope-cache", command)

    def test_remote_model_resolution_accepts_explicit_public_catalog_path(self) -> None:
        runner = self.make_runner(FakeSink(), youyun_base_model_root="")
        command = runner._model_resolution_command(
            "Qwen/Qwen3-VL-4B-Instruct",
            "",
            "/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct",
        )
        self.assertIn("/model/ModelScope/Qwen/Qwen3-VL-4B-Instruct", command)

    def test_remote_model_resolution_matches_modelscope_models_snapshot_layout(self) -> None:
        runner = self.make_runner(FakeSink())
        command = runner._model_resolution_command(
            "Qwen/Qwen2.5-VL-3B-Instruct",
            "/root/cppt/modelscope-cache",
        )
        self.assertIn(
            "/root/cppt/modelscope-cache/models/Qwen--Qwen2.5-VL-3B-Instruct/snapshots/master",
            command,
        )
        self.assertIn('[ -f "$weight" ]', command)

    def test_remote_model_resolution_accepts_explicit_model_path(self) -> None:
        runner = self.make_runner(FakeSink())
        command = runner._model_resolution_command(
            "Qwen/Qwen2.5-VL-3B-Instruct",
            "/root/models",
            "/mnt/share/models/qwen2.5-vl-3b-instruct",
        )
        self.assertIn("/mnt/share/models/qwen2.5-vl-3b-instruct", command)

    def test_remote_model_resolution_allows_explicit_path_without_root(self) -> None:
        runner = self.make_runner(FakeSink())
        command = runner._model_resolution_command(
            "Qwen/Qwen2.5-VL-3B-Instruct",
            "",
            "/mnt/share/models/qwen2.5-vl-3b-instruct",
        )
        self.assertIn("/mnt/share/models/qwen2.5-vl-3b-instruct", command)

    def test_remote_training_uses_resolved_local_model_without_modelscope_download(self) -> None:
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink).run(
                "t-local-model",
                {
                    "taskId": "t-local-model",
                    "modelRepoId": "Qwen/Qwen2.5-VL-3B-Instruct",
                    "parameters": {},
                },
            )
        )
        self.assertEqual(outcome, "completed")
        launch = next(command for command in self.ssh.commands if "llamafactory.cli train" in command)
        self.assertNotIn("USE_MODELSCOPE_HUB", launch)
        self.assertNotIn("MODELSCOPE_CACHE", launch)
        self.assertTrue(any("base-model-path:/root/models/Qwen2.5-VL-3B-Instruct" in log for log in sink.logs))

    def test_remote_model_missing_fails_without_download_fallback(self) -> None:
        class MissingModelSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "base-model-path" in command:
                    return 0, "base-model-error:no matching complete model\n"
                return super().run(command, timeout, check)

        self.ssh = MissingModelSSH()
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink).run(
                "t-missing-model",
                {
                    "taskId": "t-missing-model",
                    "modelRepoId": "Qwen/Qwen2.5-VL-3B-Instruct",
                    "parameters": {},
                },
            )
        )
        self.assertEqual(outcome, "failed")
        self.assertFalse(any("llamafactory.cli train" in command for command in self.ssh.commands))
        self.assertFalse(any("ModelScope" in log for log in sink.logs))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_remote_model_missing_does_not_download_by_default(self) -> None:
        class MissingModelSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "base-model-path" in command:
                    return 0, "base-model-error:no matching complete model\n"
                return super().run(command, timeout, check)

        self.ssh = MissingModelSSH()
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink).run(
                "t-strict-default",
                {
                    "taskId": "t-strict-default",
                    "modelRepoId": "Qwen/Qwen2.5-VL-3B-Instruct",
                    "parameters": {},
                },
            )
        )
        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "base_model_unavailable")

    def test_remote_model_missing_can_disable_modelscope_fallback(self) -> None:
        class MissingModelSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "base-model-path" in command:
                    return 0, "base-model-error:no matching complete model\n"
                return super().run(command, timeout, check)

        self.ssh = MissingModelSSH()
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(
                sink,
            ).run(
                "t-strict-local-model",
                {
                    "taskId": "t-strict-local-model",
                    "modelRepoId": "Qwen/Qwen2.5-VL-3B-Instruct",
                    "parameters": {},
                },
            )
        )
        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertIn("未找到优云本地基座模型", failure.get("error", ""))
        self.assertIn("no matching complete model", failure.get("error", ""))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])


class HappyPathTest(RunnerTestBase):
    def test_pod_create_failure_cleans_captured_instance_before_uhost_fallback(self) -> None:
        class FallbackClient:
            def describe_support_zones(self, region=None):
                return [{"region": "cn-wlcb", "zone": "cn-wlcb-02", "is_pod": False}]

        class FallbackManager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.client = FallbackClient()
                self.created_modes: list[str | None] = []

            def create_one_shot(self, task_id, **kwargs):
                self.created_modes.append(kwargs.get("instance_mode"))
                if len(self.created_modes) == 1:
                    callback = kwargs.get("on_created")
                    if callback:
                        callback("pod-leaked-unless-cleaned")
                    raise YouyunApiError("CreateCompShareInstance", 226645, "Service unavailable")
                return YouyunInstance(
                    uhost_id="uhost-fallback", name="cppt-t", state="Running", region="cn-wlcb", zone="cn-wlcb-02",
                    gpu=1, gpu_type="4090", cpu=16, memory_mb=65536, ip="1.2.3.4", ssh_port=60022,
                    ssh_user="root", password_b64="cGFzcw==", image_id="img", os_type="Linux",
                    charge_type="Postpay", instance_type="UHost", raw={},
                )

        manager = FallbackManager()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = manager
        outcome = asyncio.run(
            runner.run(
                "pod-fallback",
                {
                    "taskId": "pod-fallback",
                    "parameters": {},
                    "youyunResource": {
                        "instanceMode": "pod", "fallbackEligible": True, "region": "cn-wlcb", "zone": "cn-wlcb-01",
                        "gpuType": "4090", "gpuCount": 1, "cpuCores": 16, "memoryGb": 64, "imageId": "img", "chargeType": "Postpay",
                    },
                },
            )
        )
        self.assertEqual(outcome, "completed")
        self.assertEqual(manager.created_modes, ["pod", "uhost"])
        self.assertEqual(manager.cleaned, ["pod-leaked-unless-cleaned"])

    def test_completed(self) -> None:
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("t1", {"taskId": "t1", "parameters": {"loggingSteps": 5}}))
        self.assertEqual(outcome, "completed")
        self.assertIn("优云实例已就绪", "\n".join(sink.logs))
        self.assertTrue(any(log.startswith("GPU：NVIDIA GeForce RTX 4090") for log in sink.logs))
        # 两条训练指标被解析入库
        self.assertEqual(len(sink.metrics), 2)
        self.assertAlmostEqual(sink.metrics[0]["train_loss"], 2.31)
        # 产物收集 + 上传内容检查
        self.assertEqual(self.ssh.get_tree_calls, ["/root/cppt/t1/output"])
        remote_yaml = self.ssh.uploaded["/root/cppt/t1/train.yaml"]
        self.assertIn("/root/cppt/t1/dataset", remote_yaml)
        self.assertIn("/root/cppt/t1/output", remote_yaml)
        self.assertTrue(any("/root/cppt/cuda-library-path" in command and "LD_LIBRARY_PATH" in command for command in self.ssh.commands))
        self.assertIn("/root/cppt/t1/dataset/train.jsonl", self.ssh.uploaded)  # 数据集按文件上传
        self.assertEqual(self.manager.cleaned, [])
        marker = next(item for item in sink.artifacts if item.get("type") == "youyun-instance")
        self.assertEqual(marker["state"], "awaiting_comparison")
        self.assertTrue(marker["comparisonReady"])
        adapter = next(item for item in sink.artifacts if item.get("type") == "adapter")
        self.assertEqual(adapter["baseModelMetadata"]["maxContextLength"], 32768)
        completed = [u for u in sink.updates if "训练完成" in u.get("current_step", "")]
        self.assertTrue(completed)

    def test_non_comparison_runner_releases_completed_instance(self) -> None:
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink, retain_for_comparison=False).run(
                "visual-like", {"taskId": "visual-like", "parameters": {}}
            )
        )
        self.assertEqual(outcome, "completed")
        self.assertEqual(self.manager.cleaned, ["uhost-9"])
        marker = next(item for item in sink.artifacts if item.get("type") == "youyun-instance")
        self.assertEqual(marker["state"], "released")

    def test_yaml_paths_rewritten_to_remote(self) -> None:
        sink = FakeSink()
        asyncio.run(self.make_runner(sink).run("t1", {"taskId": "t1", "parameters": {}}))
        yaml_text = self.ssh.uploaded["/root/cppt/t1/train.yaml"]
        self.assertNotIn("/local/dataset", yaml_text)
        self.assertNotIn("/local/output", yaml_text)
        self.assertIn("bf16: true", yaml_text)

    def test_ssh_setup_failure_replaces_instance_once(self) -> None:
        class ReplacementManager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.created = 0

            def create_one_shot(self, task_id, **kwargs):
                self.created += 1
                return YouyunInstance(
                    uhost_id=f"uhost-{self.created}", name="cppt-t", state="Running", region="cn-wlcb", zone="cn-wlcb-01",
                    gpu=1, gpu_type="4090", cpu=1, memory_mb=1024, ip="1.2.3.4", ssh_port=22,
                    ssh_user="root", password_b64="cGFzcw==", image_id="img", os_type="Linux",
                    charge_type="Postpay", instance_type="UHost", raw={},
                )

        manager = ReplacementManager()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = manager
        connects = []
        def connect(instance, task_id=None):
            connects.append(instance.uhost_id)
            if instance.uhost_id == "uhost-1":
                raise RuntimeError("SSH 连接超时：阶段=SSH 协议握手")
            return self.ssh
        runner._connect_ssh = connect
        outcome = asyncio.run(runner.run("ssh-replace", {"taskId": "ssh-replace", "parameters": {}}))
        self.assertEqual(outcome, "completed")
        self.assertEqual(connects, ["uhost-1", "uhost-2"])
        self.assertEqual(manager.cleaned, ["uhost-1"])
        self.assertTrue(any("更换优云实例" in log for log in sink.logs))

    def test_gpu_unavailable_replaces_instance_once(self) -> None:
        class ReplacementManager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.created = 0

            def create_one_shot(self, task_id, **kwargs):
                self.created += 1
                return YouyunInstance(
                    uhost_id=f"uhost-{self.created}", name="cppt-t", state="Running", region="cn-wlcb", zone="cn-wlcb-01",
                    gpu=1, gpu_type="4090", cpu=1, memory_mb=1024, ip="1.2.3.4", ssh_port=22,
                    ssh_user="root", password_b64="cGFzcw==", image_id="img", os_type="Linux",
                    charge_type="Postpay", instance_type="UHost", raw={},
                )

        class BadGpuSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.gpu_polls = 0

            def run(self, command, timeout=120, check=True, **kwargs):
                if "nvidia-smi" in command:
                    self.gpu_polls += 1
                    if self.gpu_polls == 1:
                        return 0, "Failed to initialize NVML: Unknown Error\n"
                    return 0, "NVIDIA GeForce RTX 4090, 24564 MiB\n"
                return super().run(command, timeout, check)

        manager = ReplacementManager()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = manager
        ssh = BadGpuSSH()
        connects = []

        def connect(instance, task_id=None):
            connects.append(instance.uhost_id)
            return ssh

        runner._connect_ssh = connect
        outcome = asyncio.run(runner.run("gpu-replace", {"taskId": "gpu-replace", "parameters": {}}))
        self.assertEqual(outcome, "completed")
        self.assertEqual(connects, ["uhost-1", "uhost-2"])
        self.assertEqual(manager.cleaned, ["uhost-1"])
        self.assertTrue(any("GPU 不可用" in log for log in sink.logs))

    def test_cleanup_failure_is_retried_by_finally_before_task_returns(self) -> None:
        class CleanupRetryManager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.created = 0

            def create_one_shot(self, task_id, **kwargs):
                self.created += 1
                return YouyunInstance(
                    uhost_id=f"uhost-{self.created}", name="cppt-t", state="Running",
                    region="cn-wlcb", zone="cn-wlcb-01", gpu=1, gpu_type="4090",
                    cpu=1, memory_mb=1024, ip="1.2.3.4", ssh_port=22,
                    ssh_user="root", password_b64="cGFzcw==", image_id="img",
                    os_type="Linux", charge_type="Postpay", instance_type="UHost",
                    raw={},
                )

            def cleanup_after_task(self, uhost_id, stop_first=True):
                self.cleaned.append(uhost_id)
                if len(self.cleaned) == 1:
                    return {"terminated": False, "error": "transient delete failure"}
                return {"terminated": True, "checkedAt": "checked"}

        class BadGpuSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.gpu_polls = 0

            def run(self, command, timeout=120, check=True, **kwargs):
                if "nvidia-smi" in command:
                    self.gpu_polls += 1
                    if self.gpu_polls == 1:
                        return 0, "Failed to initialize NVML: Unknown Error\n"
                    return 0, "NVIDIA GeForce RTX 4090, 24564 MiB\n"
                return super().run(command, timeout, check)

        manager = CleanupRetryManager()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = manager
        ssh = BadGpuSSH()

        def connect(instance, task_id=None):
            return ssh

        runner._connect_ssh = connect
        outcome = asyncio.run(
            runner.run("cleanup-retry", {"taskId": "cleanup-retry", "parameters": {}})
        )

        self.assertEqual(outcome, "failed")
        self.assertEqual(manager.cleaned, ["uhost-1", "uhost-1"])

    def test_instance_id_is_persisted_before_wait_running_finishes(self) -> None:
        class ProvisioningManager(FakeManager):
            def __init__(self):
                super().__init__()
                self.marker_seen_during_create = False

            def create_one_shot(self, task_id, **kwargs):
                instance = YouyunInstance(
                    uhost_id="uhost-9", name="cppt-t", state="Running", region="cn-wlcb", zone="cn-wlcb-01",
                    gpu=1, gpu_type="4090", cpu=16, memory_mb=65536, ip="1.2.3.4", ssh_port=60022,
                    ssh_user="root", password_b64="cGFzcw==", image_id="img", os_type="Linux",
                    charge_type="Postpay", instance_type="UHost", raw={},
                )
                callback = kwargs.get("on_created")
                if callback:
                    callback(instance.uhost_id)
                    self.marker_seen_during_create = any(
                        item.get("instanceId") == instance.uhost_id
                        for item in sink.artifacts
                    )
                return instance

        manager = ProvisioningManager()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = manager
        outcome = asyncio.run(runner.run("provision-marker", {"taskId": "provision-marker", "parameters": {}}))
        self.assertEqual(outcome, "completed")
        self.assertTrue(manager.marker_seen_during_create)
        marker = next(item for item in sink.artifacts if item.get("type") == "youyun-instance")
        self.assertEqual(marker["instanceId"], "uhost-9")
        self.assertEqual(marker["state"], "awaiting_comparison")
        self.assertTrue(marker["comparisonReady"])

    def test_provision_cancel_releases_instance_id_captured_by_on_created(self) -> None:
        class CancelledProvisionManager(FakeManager):
            def create_one_shot(self, task_id, **kwargs):
                callback = kwargs.get("on_created")
                if callback:
                    callback("uhost-provision-cancel")
                raise YouyunProvisionCancelled("cancelled while waiting for Running")

        manager = CancelledProvisionManager()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = manager

        outcome = asyncio.run(
            runner.run(
                "provision-cancel",
                {"taskId": "provision-cancel", "parameters": {}},
            )
        )

        self.assertEqual(outcome, "stopped")
        self.assertEqual(manager.cleaned, ["uhost-provision-cancel"])
        marker = next(item for item in sink.artifacts if item.get("type") == "youyun-instance")
        self.assertEqual(marker["state"], "released")

    def test_on_created_persistence_failure_still_releases_captured_instance(self) -> None:
        class FailingMarkerSink(FakeSink):
            def update(self, task_id, **changes) -> None:
                if "artifacts" in changes:
                    raise RuntimeError("marker persistence failed")
                super().update(task_id, **changes)

        class CallbackFailureManager(FakeManager):
            def create_one_shot(self, task_id, **kwargs):
                callback = kwargs.get("on_created")
                if callback:
                    callback("uhost-callback-failure")
                raise AssertionError("on_created callback should have failed")

        manager = CallbackFailureManager()
        sink = FailingMarkerSink()
        runner = self.make_runner(sink)
        runner.manager = manager

        outcome = asyncio.run(
            runner.run(
                "callback-failure",
                {"taskId": "callback-failure", "parameters": {}},
            )
        )

        self.assertEqual(outcome, "failed")
        self.assertEqual(manager.cleaned, ["uhost-callback-failure"])
        self.assertTrue(any(update.get("cleanup_state") == "released" for update in sink.updates))

    def test_ssh_timeout_has_dedicated_failure_code(self) -> None:
        self.assertEqual(
            _failure_code(RuntimeError("SSH 连接超时：阶段=SSH 协议握手"), "ssh_connect"),
            "ssh_handshake_timeout",
        )

    def test_create_service_unavailable_has_provider_failure_code(self) -> None:
        self.assertEqual(
            _failure_code(
                RuntimeError("优云 API CreateCompShareInstance 失败（RetCode=226645）：Service unavailable"),
                "instance_provision",
            ),
            "youyun_create_service_unavailable",
        )

    def test_create_service_unavailable_without_instance_marks_cleanup_terminal(self) -> None:
        class UnavailableManager(FakeManager):
            def create_one_shot(self, task_id, **kwargs):
                raise RuntimeError(
                    "优云 API CreateCompShareInstance 失败（RetCode=226645）：Service unavailable"
                )

        sink = FakeSink()
        runner = self.make_runner(sink)
        runner.manager = UnavailableManager()

        outcome = asyncio.run(
            runner.run(
                "create-unavailable-lifecycle",
                {"taskId": "create-unavailable-lifecycle", "provider": "youyun", "parameters": {}},
            )
        )

        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "youyun_create_service_unavailable")
        self.assertEqual(failure.get("resource_state"), "released")
        self.assertEqual(failure.get("cleanup_state"), "released")
        self.assertIsNotNone(failure.get("cleanup_checked_at"))
        self.assertTrue(any("未创建实例，无需释放" in message for message in sink.logs))

    def test_connection_refused_is_ssh_failure_not_dependency_failure(self) -> None:
        from paramiko.ssh_exception import NoValidConnectionsError
        error = NoValidConnectionsError({("192.0.2.1", 23): ConnectionRefusedError(111, "refused")})
        self.assertEqual(_failure_code(error, "dependency_install"), "ssh_channel_disconnected")

    def test_failure_code_classifies_gpu_driver_unavailable(self) -> None:
        self.assertEqual(
            _failure_code(RuntimeError("Failed to initialize NVML: Unknown Error"), "gpu_probe"),
            "gpu_unavailable",
        )

    def test_failure_code_keeps_artifact_filesystem_errors_out_of_ssh_bucket(self) -> None:
        self.assertEqual(
            _failure_code(OSError("No space left on device"), "artifact_collection"),
            "artifact_collection_failed",
        )

    def test_failure_code_keeps_upload_filesystem_errors_out_of_ssh_bucket(self) -> None:
        self.assertEqual(
            _failure_code(FileNotFoundError("dataset file missing"), "upload"),
            "remote_training_failed",
        )

    def test_failure_code_keeps_training_timeout_out_of_ssh_bucket(self) -> None:
        self.assertEqual(
            _failure_code(TimeoutError("timed out"), "training"),
            "training_timeout",
        )


class FailurePathTest(RunnerTestBase):
    def test_installer_launch_recovers_with_real_ssh_client_after_probe(self) -> None:
        import io
        from paramiko.ssh_exception import NoValidConnectionsError
        from backend.providers.youyun.ssh import YouyunSSHClient

        class Connection:
            active = True
            def get_transport(self):
                return SimpleNamespace(is_active=lambda: self.active)
            def close(self):
                self.active = False
            def exec_command(connection, command, timeout):
                code, output = FakeSSH.run(self.ssh, command, timeout)
                # The cleanup command immediately preceding the installer launch
                # has no generation assignment; drop its connection after reply.
                if "find /tmp" in command and "RESET=" not in command:
                    connection.active = False
                stdout = io.BytesIO(output.encode())
                stdout.channel = SimpleNamespace(recv_exit_status=lambda: code)
                return io.BytesIO(), stdout, io.BytesIO()

        class Transport:
            calls = 0
            def connect(transport, **kwargs):
                transport.calls += 1
                if transport.calls == 2:
                    raise NoValidConnectionsError({("192.0.2.1", 23): ConnectionRefusedError(111, "refused")})
                return Connection()

        transport = Transport()
        sink = FakeSink()
        real = YouyunSSHClient(transport=transport, retry_backoff=0, on_connection_retry=lambda message: sink.append_log("recovery", message))
        self.ssh.connect = real.connect
        self.ssh.run = real.run
        self.ssh.close = real.close
        runner = self.make_runner(sink)
        outcome = asyncio.run(runner.run("recovery", {"taskId": "recovery", "parameters": {}}))
        self.assertEqual(outcome, "completed")
        self.assertEqual(transport.calls, 3)
        launches = [command for command in self.ssh.commands if "RESET=true" in command]
        self.assertEqual(len(launches), 1)
        self.assertTrue(any("errno=111" in message for message in sink.logs))

    def test_idempotent_gpu_probe_reconnects_after_channel_eof(self) -> None:
        class FlakyGPUQuerySSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.gpu_failures = 1
                self.reconnect_calls = 0

            def run(self, command, timeout=120, check=True, **kwargs):
                if "nvidia-smi" in command and self.gpu_failures:
                    self.gpu_failures -= 1
                    raise EOFError()
                return super().run(command, timeout, check)

            def reconnect(self) -> None:
                self.reconnect_calls += 1

        self.ssh = FlakyGPUQuerySSH()
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink).run(
                "gpu-query-retry",
                {"taskId": "gpu-query-retry", "modelRepoId": "Qwen/Qwen2.5-VL-3B-Instruct", "parameters": {}},
            )
        )
        self.assertEqual(outcome, "completed")
        self.assertEqual(self.ssh.reconnect_calls, 1)

    def test_missing_base_model_persists_actionable_failure_code(self) -> None:
        class MissingModelSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "base-model-path" in command:
                    return 0, "base-model-error:no matching complete model\n"
                return super().run(command, timeout, check)

        self.ssh = MissingModelSSH()
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink).run(
                "missing-model-code",
                {"taskId": "missing-model-code", "modelRepoId": "Qwen/Qwen2-VL-2B-Instruct", "parameters": {}},
            )
        )
        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "base_model_unavailable")
        self.assertEqual(failure.get("phase"), "model_resolution")
        self.assertIn("基座模型", failure.get("suggested_action", ""))

    def test_ssh_eof_persists_channel_failure_code(self) -> None:
        class EOFSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "base-model-path" in command:
                    raise EOFError()
                return super().run(command, timeout, check)

        self.ssh = EOFSSH()
        sink = FakeSink()
        outcome = asyncio.run(
            self.make_runner(sink).run(
                "ssh-eof-code",
                {"taskId": "ssh-eof-code", "modelRepoId": "Qwen/Qwen2-VL-2B-Instruct", "parameters": {}},
            )
        )
        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "ssh_channel_disconnected")
        self.assertEqual(failure.get("phase"), "model_resolution")

    def test_missing_youyun_configuration_persists_configuration_failure_code(self) -> None:
        class ConfigManager(FakeManager):
            def create_one_shot(self, task_id, **kwargs):
                raise RuntimeError("优云配置缺少：YOUYUN_PUBLIC_KEY, YOUYUN_PRIVATE_KEY")

        sink = FakeSink()
        runner = YouyunTrainingRunner(
            manager=ConfigManager(),
            sink=sink,
            ssh_factory=lambda: self.ssh,
            sleep=lambda s: None,
            clock=_FakeClock(),
        )
        outcome = asyncio.run(runner.run("config-code", {"taskId": "config-code", "parameters": {}}))
        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "youyun_config_missing")
        self.assertEqual(failure.get("phase"), "instance_provision")
    def test_repeated_transient_training_poll_disconnects_reconnect_and_continues(self) -> None:
        class FlakyPollSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.poll_failures = 0
                self.reset_calls = 0

            def run(self, command, timeout=120, check=True):
                if command.startswith("echo code:") and self.poll_failures < 2:
                    self.poll_failures += 1
                    raise EOFError()
                return super().run(command, timeout, check)

            def reset_connection(self) -> None:
                self.reset_calls += 1

        self.ssh = FlakyPollSSH()
        sink = FakeSink()

        outcome = asyncio.run(
            self.make_runner(sink).run(
                "poll-eof-retry",
                {"taskId": "poll-eof-retry", "parameters": {"loggingSteps": 5}},
            )
        )

        self.assertEqual(outcome, "completed")
        self.assertEqual(self.ssh.reset_calls, 2)
        self.assertTrue(any("EOFError" in log for log in sink.logs))

    def test_training_poll_timeout_reconnects_and_continues(self) -> None:
        class FlakyPollSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.poll_timeouts = 0
                self.reset_calls = 0

            def run(self, command, timeout=120, check=True):
                if command.startswith("echo code:") and self.poll_timeouts == 0:
                    self.poll_timeouts += 1
                    raise TimeoutError()
                return super().run(command, timeout, check)

            def reset_connection(self) -> None:
                self.reset_calls += 1

        self.ssh = FlakyPollSSH()
        sink = FakeSink()

        outcome = asyncio.run(
            self.make_runner(sink).run(
                "poll-timeout",
                {"taskId": "poll-timeout", "parameters": {"loggingSteps": 5}},
            )
        )

        self.assertEqual(outcome, "completed")
        self.assertEqual(self.ssh.reset_calls, 1)
        self.assertIn("TimeoutError", "\n".join(sink.logs))

    def test_repeated_empty_poll_error_is_persisted_with_exception_type(self) -> None:
        class BrokenPollSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if command.startswith("echo code:"):
                    raise TimeoutError()
                return super().run(command, timeout, check)

            def reset_connection(self) -> None:
                return None

        self.ssh = BrokenPollSSH()
        sink = FakeSink()

        outcome = asyncio.run(
            self.make_runner(sink, training_timeout=6).run(
                "poll-broken",
                {"taskId": "poll-broken", "parameters": {}},
            )
        )

        self.assertEqual(outcome, "failed")
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertIn("TimeoutError", failure.get("error", ""))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_ssh_banner_failure_retries_and_then_succeeds(self) -> None:
        class FlakySSH(FakeSSH):
            attempts = 0

            def connect(self, host, port, user, password) -> None:
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError("Error reading SSH protocol banner")

        self.ssh = FlakySSH()
        sink = FakeSink()
        runner = self.make_runner(sink)
        connected = runner._connect_ssh(self.manager.create_one_shot("ssh-banner"), "ssh-banner")
        self.assertIs(connected, self.ssh)
        self.assertEqual(self.ssh.attempts, 2)
        self.assertTrue(any("协议握手未完成" in log for log in sink.logs))
        self.assertTrue(any("SSH 连接已建立" in log for log in sink.logs))

    def test_initial_auth_transport_eof_waits_for_sshd(self) -> None:
        from paramiko.ssh_exception import AuthenticationException
        class StartingSSH(FakeSSH):
            attempts = 0
            def connect(self, host, port, user, password):
                self.attempts += 1
                if self.attempts == 1:
                    raise AuthenticationException("Authentication failed: transport shut down or saw EOF")
        self.ssh = StartingSSH()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner._connect_ssh(self.manager.create_one_shot("auth-eof"), "auth-eof")
        self.assertEqual(self.ssh.attempts, 2)

    def test_ssh_auth_failure_does_not_retry(self) -> None:
        class AuthSSH(FakeSSH):
            attempts = 0

            def connect(self, host, port, user, password) -> None:
                self.attempts += 1
                raise RuntimeError("Authentication failed")

        self.ssh = AuthSSH()
        sink = FakeSink()
        runner = self.make_runner(sink)
        with self.assertRaisesRegex(RuntimeError, "认证失败"):
            runner._connect_ssh(self.manager.create_one_shot("ssh-auth"), "ssh-auth")
        self.assertEqual(self.ssh.attempts, 1)

    def test_ssh_retries_with_refreshed_port(self) -> None:
        class RefreshManager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.refreshes = 0

            def get(self, uhost_id):
                self.refreshes += 1
                instance = self.create_one_shot(uhost_id)
                if self.refreshes >= 2:
                    instance.ssh_port = 23
                return instance

        class PortSSH(FakeSSH):
            ports: list[int] = []

            def connect(self, host, port, user, password) -> None:
                self.ports.append(port)
                if port != 23:
                    raise TimeoutError("timed out")

        self.manager = RefreshManager()
        self.ssh = PortSSH()
        sink = FakeSink()
        runner = self.make_runner(sink)
        runner._connect_ssh(self.manager.create_one_shot("ssh-refresh"), "ssh-refresh")
        self.assertEqual(self.ssh.ports, [60022, 23])
        self.assertGreaterEqual(self.manager.refreshes, 2)

    def test_install_failure_cleans_instance(self) -> None:
        self.ssh = FakeSSH(install_polls=0, install_fail=True)
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("t2", {"taskId": "t2"}))
        self.assertEqual(outcome, "failed")
        self.assertTrue(any(u.get("state") == "failed" for u in sink.updates))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_nonzero_train_exit_returns_failed(self) -> None:
        class FailSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if command.startswith("echo code:"):
                    self.train_poll_count += 1
                    if self.train_poll_count > len(LOG_LINES):
                        return 0, "code:1\nRuntimeError: OOM\n"
                    return 0, f"code:none\n{LOG_LINES[self.train_poll_count - 1]}\n"
                return super().run(command, timeout, check)

        self.ssh = FailSSH()
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("t3", {"taskId": "t3"}))
        self.assertEqual(outcome, "failed")
        self.assertTrue(any(u.get("state") == "failed" for u in sink.updates))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_cleanup_failure_recorded_not_raised(self) -> None:
        self.manager.cleanup_error = "账户欠费"
        self.ssh = FakeSSH(install_polls=0, install_fail=True)
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("t4", {"taskId": "t4"}))
        self.assertEqual(outcome, "failed")
        self.assertIn("清理未完成：账户欠费", "\n".join(sink.logs))
        marker = next(item for item in sink.artifacts if item.get("type") == "youyun-instance")
        self.assertEqual(marker["state"], "cleanup_failed")
        self.assertEqual(marker["cleanupError"], "账户欠费")

    def test_install_launch_failure_reports_launcher_error(self) -> None:
        class LaunchFailSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if command.startswith("cat /tmp/env-install.done"):
                    return 0, "launch-failed\n---\nnohup: command not found\n"
                return super().run(command, timeout, check)

        self.ssh = LaunchFailSSH()
        runner = self.make_runner(FakeSink(), install_timeout=20)
        with self.assertRaisesRegex(RuntimeError, "启动失败"):
            asyncio.run(runner._install_environment("launch-fail", self.ssh))

    def test_install_does_not_fail_on_transient_pid_visibility_race(self) -> None:
        class PidRaceSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.polls = 0

            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.polls += 1
                    if self.polls == 1:
                        return 0, "install-started\ninstall-dead\n---\n"
                    if self.polls == 2:
                        return 0, "install-started\ninstall-running\n---\n"
                    return 0, "ok\ninstall-started\npython-path-ready\ninstall-dead\n---\nfinished\n"
                return super().run(command, timeout, check)

        self.ssh = PidRaceSSH()
        runner = self.make_runner(FakeSink(), install_timeout=20, poll_interval=0)
        asyncio.run(runner._install_environment("pid-race", self.ssh))

    def test_install_does_not_treat_launch_ok_as_completion(self) -> None:
        class LaunchRunningSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.commands.append(command)
                    if self.install_poll_count == 0:
                        self.install_poll_count += 1
                        return 0, "install-started\nlaunch-ok\ninstall-running\n---\nstill installing\n"
                    return 0, "ok\npython-path-ready\n---\nfinished\n"
                return super().run(command, timeout, check)

        self.ssh = LaunchRunningSSH()
        runner = self.make_runner(FakeSink(), install_timeout=20, poll_interval=0)
        asyncio.run(runner._install_environment("launch-ok-race", self.ssh))


class StopPathTest(RunnerTestBase):
    def test_cleanup_is_retried_after_intermediate_release_failure(self) -> None:
        """A failed replacement cleanup must not suppress the final retry."""
        class SequenceCleanupManager(FakeManager):
            def __init__(self) -> None:
                super().__init__()
                self.cleanup_calls = 0

            def cleanup_after_task(self, uhost_id, stop_first=True):
                self.cleanup_calls += 1
                self.cleaned.append(uhost_id)
                if self.cleanup_calls == 1:
                    return {"terminated": False, "error": "provider temporarily unavailable"}
                return {"terminated": True, "error": None, "checkedAt": "now"}

        manager = SequenceCleanupManager()
        sink = FakeSink()
        runner = self.make_runner(sink, ssh_instance_retries=1)
        runner.manager = manager

        def fail_connect(instance, task_id=None):
            raise RuntimeError("SSH 连接失败（1.2.3.4:60022）：connection refused")

        runner._connect_ssh = fail_connect  # type: ignore[method-assign]
        outcome = asyncio.run(runner.run("cleanup-retry", {"taskId": "cleanup-retry"}))

        self.assertEqual(outcome, "failed")
        self.assertEqual(manager.cleanup_calls, 2)
        self.assertEqual(manager.cleaned, ["uhost-9", "uhost-9"])
        self.assertEqual(next(update for update in reversed(sink.updates) if "cleanup_state" in update)["cleanup_state"], "released")

    def test_stop_remote_install_removes_generation_scoped_markers(self) -> None:
        asyncio.run(self.make_runner(FakeSink())._stop_remote_install(self.ssh))
        cleanup = next(command for command in self.ssh.commands if "env-install.done" in command)
        self.assertIn("find /tmp", cleanup)
        self.assertIn("env-install.*.*", cleanup)
        self.assertNotIn("rm -rf /tmp/env-install.*", cleanup)

    def test_stop_remote_install_verifies_pid_commandline_before_kill(self) -> None:
        asyncio.run(self.make_runner(FakeSink())._stop_remote_install(self.ssh))
        cleanup = next(command for command in self.ssh.commands if "env-install.done" in command)
        self.assertIn("/proc/$p/cmdline", cleanup)
        self.assertIn("/tmp/env-install.sh", cleanup)
        self.assertIn("OLD_GEN", cleanup)

    def test_cancel_during_instance_creation_releases_instance_after_worker_returns(self) -> None:
        """A cancelled to_thread await must not orphan an instance created later."""
        entered = threading.Event()
        release = threading.Event()

        class DelayedCreateManager(FakeManager):
            def create_one_shot(self, task_id, **kwargs):
                entered.set()
                release.wait(timeout=5)
                callback = kwargs.get("on_created")
                if callback:
                    callback("uhost-delayed")
                return YouyunInstance(
                    uhost_id="uhost-delayed", name="cppt-t", state="Running", region="cn-wlcb", zone="cn-wlcb-01",
                    gpu=1, gpu_type="4090", cpu=16, memory_mb=65536, ip="1.2.3.4", ssh_port=60022,
                    ssh_user="root", password_b64="cGFzcw==", image_id="img", os_type="Linux",
                    charge_type="Postpay", instance_type="UHost", raw={},
                )

        self.manager = DelayedCreateManager()
        sink = FakeSink()
        runner = self.make_runner(sink)

        async def exercise() -> None:
            pending = asyncio.create_task(runner.run("cancel-race", {"taskId": "cancel-race", "parameters": {}}))
            await asyncio.to_thread(entered.wait, 2)
            pending.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            # Give the detached provider thread and its cleanup callback a turn.
            for _ in range(20):
                if self.manager.cleaned:
                    break
                await asyncio.sleep(0.01)

        asyncio.run(exercise())
        self.assertEqual(self.manager.cleaned, ["uhost-delayed"])

    def test_manual_stop(self) -> None:
        sink = FakeSink(cancel_after_logs=6)
        events: list[str] = []
        original_cleanup = self.manager.cleanup_after_task

        def cleanup(*args, **kwargs):
            events.append("cleanup")
            return original_cleanup(*args, **kwargs)

        self.manager.cleanup_after_task = cleanup
        original_update = sink.update

        def update(task_id, **changes):
            if changes.get("state") == "stopped":
                events.append("stopped")
            return original_update(task_id, **changes)

        sink.update = update
        outcome = asyncio.run(self.make_runner(sink).run("t5", {"taskId": "t5", "parameters": {}}))
        self.assertEqual(outcome, "stopped")
        self.assertTrue(any(u.get("state") == "stopped" for u in sink.updates))
        self.assertEqual(self.manager.cleaned, [])
        marker = next(item for item in sink.artifacts if item.get("type") == "youyun-instance")
        self.assertEqual(marker["state"], "awaiting_comparison")
        self.assertTrue(marker["comparisonReady"])
        self.assertNotIn("cleanup", events)

    def test_manual_stop_during_install_does_not_start_training(self) -> None:
        self.ssh = FakeSSH(install_polls=20)
        sink = FakeSink(cancel_after_logs=3)
        outcome = asyncio.run(self.make_runner(sink).run("t6", {"taskId": "t6", "parameters": {}}))
        self.assertEqual(outcome, "stopped")
        self.assertTrue(any(u.get("current_step") == "正在停止远程环境安装" for u in sink.updates))
        self.assertTrue(any("/tmp/env-install.pid" in command for command in self.ssh.commands))
        self.assertTrue(any("</dev/null" in command for command in self.ssh.commands))
        self.assertIn("/tmp/env-install.sh", self.ssh.uploaded)
        self.assertFalse(any("llamafactory.cli train" in command for command in self.ssh.commands))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_install_process_exit_without_marker_fails_fast(self) -> None:
        self.ssh = FakeSSH(install_process_dead=True)
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("t7", {"taskId": "t7", "parameters": {}}))
        self.assertEqual(outcome, "failed")
        self.assertIn("远程依赖安装进程已退出但未生成完成标记", "\n".join(sink.logs))
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_install_restarts_once_after_process_dies_before_success(self) -> None:
        class RestartSuccessSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.install_poll_count = 0

            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.install_poll_count += 1
                    if self.install_poll_count <= 5:
                        return 0, "install-started\ninstall-dead\n---\n"
                    return 0, "ok\npython-path-ready\n---\nfinished\n"
                return super().run(command, timeout, check)

        self.ssh = RestartSuccessSSH()
        sink = FakeSink()
        runner = self.make_runner(sink, install_timeout=30, poll_interval=0)
        asyncio.run(runner._install_environment("install-restart", self.ssh))
        self.assertTrue(any("重新启动安装" in log for log in sink.logs))

    def test_live_launcher_pid_prevents_false_install_dead_restart(self) -> None:
        class LiveLauncherSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.status_polls = 0
                self.launch_commands: list[str] = []

            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.status_polls += 1
                    if self.status_polls <= 10:
                        return 0, (
                            "install-started\n"
                            "install-dead\n"
                            "launch-pid:4242\n"
                            "launcher-running\n"
                            "---\n"
                            "installer child exited while launcher is still alive\n"
                        )
                    return 0, "ok\npython-path-ready\n---\nfinished\n"
                if "ROOT=/tmp/env-install.$GEN" in command:
                    self.launch_commands.append(command)
                return super().run(command, timeout, check)

        self.ssh = LiveLauncherSSH()
        sink = FakeSink()
        runner = self.make_runner(sink, install_timeout=60, poll_interval=0)
        asyncio.run(runner._install_environment("live-launcher", self.ssh))
        self.assertEqual(len(self.ssh.launch_commands), 1)

    def test_install_restart_kills_child_and_launcher_before_clearing_markers(self) -> None:
        class RestartCleanupSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.status_polls = 0
                self.launch_commands: list[str] = []

            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.status_polls += 1
                    if self.status_polls <= 10:
                        return 0, "install-started\ninstall-dead\n---\n"
                    return 0, "ok\npython-path-ready\n---\nfinished\n"
                if "ROOT=/tmp/env-install.$GEN" in command:
                    self.launch_commands.append(command)
                return super().run(command, timeout, check)

        self.ssh = RestartCleanupSSH()
        sink = FakeSink()
        runner = self.make_runner(sink, install_timeout=60, poll_interval=0)
        asyncio.run(runner._install_environment("restart-cleanup", self.ssh))

        self.assertGreaterEqual(len(self.ssh.launch_commands), 2)
        force_launch = self.ssh.launch_commands[1]
        marker_reset = force_launch.index("rm -f /tmp/env-install.done")
        self.assertIn("/tmp/env-install.pid", force_launch)
        self.assertIn("/tmp/env-install.launch.pid", force_launch)
        self.assertIn("kill -TERM", force_launch)
        self.assertLess(force_launch.index("/tmp/env-install.pid"), marker_reset)
        self.assertLess(force_launch.index("/tmp/env-install.launch.pid"), marker_reset)

    def test_install_success_requires_persisted_python_path(self) -> None:
        class MissingPythonPathSSH(FakeSSH):
            install_python_path_missing = True

        self.ssh = MissingPythonPathSSH()
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("missing-python-path", {"taskId": "missing-python-path"}))
        self.assertEqual(outcome, "failed")
        self.assertIn("python-path", "\n".join(sink.logs))
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "dependency_python_path_missing")
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_training_start_requires_python_path_after_install(self) -> None:
        class DisappearingPythonPathSSH(FakeSSH):
            def run(self, command, timeout=120, check=True):
                if "python-path" in command and "env-install.done" not in command:
                    self.commands.append(command)
                    return 0, "python-path-missing\n"
                return super().run(command, timeout, check)

        self.ssh = DisappearingPythonPathSSH()
        sink = FakeSink()
        outcome = asyncio.run(self.make_runner(sink).run("training-python-path", {"taskId": "training-python-path"}))
        self.assertEqual(outcome, "failed")
        self.assertIn("python-path", "\n".join(sink.logs))
        failure = next(update for update in reversed(sink.updates) if update.get("state") == "failed")
        self.assertEqual(failure.get("failure_code"), "dependency_python_path_missing")
        self.assertEqual(self.manager.cleaned, ["uhost-9"])

    def test_install_launcher_records_runtime_diagnostics_and_uses_detached_script(self) -> None:
        runner = self.make_runner(FakeSink(), install_timeout=20)
        asyncio.run(runner._install_environment("launcher-diagnostics", self.ssh))
        launch_commands = [command for command in self.ssh.commands if "env-install" in command]
        self.assertTrue(any("command -v bash" in command for command in launch_commands))
        self.assertTrue(any("setsid" in command for command in launch_commands))
        self.assertTrue(any('"$SETSID" "$BASH" /tmp/env-install.sh' in command for command in launch_commands))
        self.assertTrue(any('"$NOHUP" "$SETSID" "$BASH" /tmp/env-install.sh' in command for command in launch_commands))
        self.assertTrue(any("env-install.launch" in command for command in launch_commands))
        self.assertTrue(any("env-install.launch.pid" in command for command in launch_commands))
        self.assertTrue(any(": > /tmp/env-install.log" in command for command in launch_commands))
        self.assertTrue(any(".tmp" in command and "mv" in command for command in launch_commands))
        self.assertTrue(any(path.endswith("/env-install.sh") for path in self.ssh.uploaded))
        install_script = next(text for path, text in self.ssh.uploaded.items() if path.endswith("/env-install.sh"))
        self.assertIn("ok\\npython-path-ready\\n", install_script)
        self.assertIn('echo $$ > "$PID_FILE.tmp"', install_script)

    def test_install_launcher_creates_generation_marker_directory_before_redirects(self) -> None:
        """Generation-scoped marker files must not be written into a missing directory."""
        runner = self.make_runner(FakeSink(), install_timeout=20)
        command = runner._install_launch_command("0123456789abcdef", force=True)

        self.assertIn('mkdir -p "$ROOT"', command)
        self.assertLess(command.index('mkdir -p "$ROOT"'), command.index('date -Iseconds > "$ROOT.started"'))

    def test_install_launcher_wraps_setsid_with_nohup_to_survive_ssh_close(self) -> None:
        runner = self.make_runner(FakeSink(), install_timeout=20)
        asyncio.run(runner._install_environment("launcher-nohup", self.ssh))
        launch_commands = [command for command in self.ssh.commands if "env-install" in command]
        self.assertTrue(
            any('"$NOHUP" "$SETSID" "$BASH" /tmp/env-install.sh' in command for command in launch_commands)
        )

    def test_install_treats_live_launcher_as_running_before_child_pid_is_visible(self) -> None:
        class LauncherPidRaceSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.polls = 0

            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.polls += 1
                    if self.polls <= 12:
                        return 0, "install-started\ninstall-dead\ninstall-launcher-running\nlaunch-pid:4321\n---\n"
                    return 0, "ok\npython-path-ready\n---\nfinished\n"
                return super().run(command, timeout, check)

        self.ssh = LauncherPidRaceSSH()
        sink = FakeSink()
        runner = self.make_runner(sink, install_timeout=30, poll_interval=0)
        asyncio.run(runner._install_environment("launcher-pid-race", self.ssh))
        self.assertFalse(any("重新启动安装" in log for log in sink.logs))

    def test_install_restart_stops_old_launcher_before_relaunch(self) -> None:
        class RestartWithLauncherSSH(FakeSSH):
            def __init__(self) -> None:
                super().__init__()
                self.polls = 0

            def run(self, command, timeout=120, check=True):
                if "env-install.done" in command and "cat" in command:
                    self.polls += 1
                    if self.polls <= 12:
                        return 0, "install-started\ninstall-dead\ninstall-launcher-dead\nlaunch-pid:9876\n---\n"
                    return 0, "ok\npython-path-ready\n---\nfinished\n"
                return super().run(command, timeout, check)

        self.ssh = RestartWithLauncherSSH()
        runner = self.make_runner(FakeSink(), install_timeout=30, poll_interval=0)
        asyncio.run(runner._install_environment("restart-old-launcher", self.ssh))
        stop_commands = [command for command in self.ssh.commands if "env-install.launch.pid" in command]
        self.assertTrue(stop_commands)
        self.assertTrue(any("kill -TERM" in command for command in stop_commands))


if __name__ == "__main__":
    unittest.main()
