from __future__ import annotations

"""LLaMA-Factory executor for already-materialized visual tasks."""

from pathlib import Path
from typing import Any, Mapping

from backend.local_llamafactory_executor import LocalLlamaFactoryExecutor
from backend.training_executor import ExecutionHandle


class LocalVisionTrainingExecutor(LocalLlamaFactoryExecutor):
    """Run the YAML and multimodal bundle produced by ``vision_training``.

    The regular text executor creates a dataset contract from a text task. A
    visual task already owns a validated ShareGPT multimodal bundle, so its
    preparation step only validates the bounded preview directory and reuses
    the inherited process, event, stop, and artifact lifecycle.
    """

    def __init__(self, *, work_root: Path, command: str | list[str] | None = None,
                 python_executable: str | None = None, allowed_root: Path | None = None) -> None:
        super().__init__(
            work_root=work_root,
            command=command,
            python_executable=python_executable,
        )
        self.allowed_root = (allowed_root or work_root).expanduser().resolve()

    async def prepare(self, task: Mapping[str, Any]) -> ExecutionHandle:
        task_id = self._task_id(task)
        root_value = task.get("previewRoot") or task.get("root")
        if not root_value:
            raise ValueError("视觉训练任务缺少已物化的数据包目录。")
        root = Path(str(root_value)).expanduser().resolve()
        if root == self.allowed_root or self.allowed_root not in root.parents:
            raise ValueError("视觉训练数据包目录不在受控运行目录内。")
        config_value = task.get("configPath") or (root / "train.yaml")
        config_path = Path(str(config_value)).expanduser().resolve()
        output_dir = (root / "output").resolve()
        dataset_dir = (root / "dataset").resolve()
        for path, label in ((config_path, "训练配置"), (dataset_dir, "视觉数据集目录")):
            if not path.is_relative_to(root) or not path.exists():
                raise ValueError(f"视觉任务{label}不存在或路径越界：{path}")
        output_dir.mkdir(parents=True, exist_ok=True)
        handle = ExecutionHandle(
            task_id=task_id,
            workdir=str(root),
            config_path=str(config_path),
            prepared=True,
        )
        self._handles[task_id] = handle
        return handle


__all__ = ["LocalVisionTrainingExecutor"]
