import asyncio
import shlex
import unittest

from backend.youyun_vision_probe import VisionProbeRequest, evaluate_probe_results, probe_remote_vision_environment


class _SyntaxCheckingSSH:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command, timeout, check):
        self.commands.append(command)
        parts = shlex.split(command)
        if parts[:2] == ["python3", "-c"]:
            compile(parts[2], "<remote-probe>", "exec")
        return 0, "ok"


class YouyunVisionProbeTests(unittest.TestCase):
    def test_probe_reports_missing_dependency_without_claiming_passed(self) -> None:
        result = evaluate_probe_results(
            VisionProbeRequest(
                repo_id="Qwen/Qwen2.5-VL-3B-Instruct",
                model_path="/model/Qwen2.5-VL-3B-Instruct",
                template="qwen2_vl",
                tuning_mode="qlora",
            ),
            {
                "python": 0,
                "torch": 0,
                "dependencies": 1,
                "processor": 0,
                "lora": 0,
                "forward": 0,
            },
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.blocking_reason, "dependencies_missing")
        self.assertEqual(result.checks["dependencies"]["status"], "failed")

    def test_probe_accepts_small_qwen_vl_when_processor_and_lora_pass(self) -> None:
        result = evaluate_probe_results(
            VisionProbeRequest(
                repo_id="Qwen/Qwen2.5-VL-3B-Instruct",
                model_path="/model/Qwen2.5-VL-3B-Instruct",
                template="qwen2_vl",
                tuning_mode="qlora",
            ),
            {
                "python": 0,
                "torch": 0,
                "dependencies": 0,
                "processor": 0,
                "lora": 0,
                "forward": 0,
            },
        )

        self.assertTrue(result.passed)
        self.assertEqual(result.recommended_tuning_mode, "qlora")
        self.assertEqual(result.checks["forward"]["status"], "passed")


class YouyunVisionRemoteProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_processor_command_quotes_repo_and_template_as_python_literals(self) -> None:
        ssh = _SyntaxCheckingSSH()
        result = await probe_remote_vision_environment(
            ssh,
            VisionProbeRequest(
                repo_id="Qwen/Qwen2.5-VL-3B-Instruct",
                model_path="/model/ModelScope/Qwen/Qwen2.5-VL-3B-Instruct",
                template="qwen2_vl",
                tuning_mode="qlora",
            ),
        )

        self.assertTrue(result.passed)
        self.assertEqual(len(ssh.commands), 6)


if __name__ == "__main__":
    unittest.main()
