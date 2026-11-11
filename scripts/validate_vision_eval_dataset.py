from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PLACEHOLDER_MARKERS = ("待填写", "TODO", "REPLACE_ME")


class ValidationReport:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.image_hashes: dict[str, str] = {}
        self.group_ids: set[str] = set()
        self.task_types: Counter[str] = Counter()
        self.domains: Counter[str] = Counter()
        self.difficulties: Counter[str] = Counter()
        self.reviewed_samples = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_safe_relative_path(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and bool(value.strip())


def image_signature_ok(path: Path) -> bool:
    header = path.read_bytes()[:16]
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return header[:3] == b"\xff\xd8\xff"
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix == ".webp":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    return False


def validate_json_answer(record: dict[str, Any], line_number: int, report: ValidationReport) -> None:
    if record.get("expected_format") != "json":
        return
    answer = record.get("answer") or record.get("text")
    try:
        json.loads(answer)
    except (TypeError, json.JSONDecodeError):
        report.errors.append(
            f"第 {line_number} 行 expected_format=json，但 answer 不是合法 JSON。"
        )


def load_reference_metadata(path: Path) -> tuple[set[str], set[str]]:
    image_hashes: set[str] = set()
    group_ids: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                if isinstance(record.get("group_id"), str):
                    group_ids.add(record["group_id"])
                recorded_hash = record.get("image_hash")
                if isinstance(recorded_hash, str) and recorded_hash:
                    image_hashes.add(recorded_hash)
                file_name = record.get("file_name") or record.get("image")
                if isinstance(file_name, str) and is_safe_relative_path(file_name):
                    image_path = (path.parent / file_name).resolve()
                    if image_path.is_file():
                        image_hashes.add(sha256_file(image_path))
    return image_hashes, group_ids


def validate_dataset(
    root: Path,
    metadata_path: Path,
    *,
    reference_metadata: list[Path],
    allow_unreviewed: bool,
) -> ValidationReport:
    report = ValidationReport()
    seen_sample_ids: set[str] = set()
    seen_file_names: set[str] = set()
    same_hash_groups: defaultdict[str, set[str]] = defaultdict(set)
    reference_hashes: set[str] = set()
    reference_groups: set[str] = set()

    for path in reference_metadata:
        if not path.is_file():
            report.errors.append(f"参考标注文件不存在：{path}")
            continue
        try:
            hashes, groups = load_reference_metadata(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            report.errors.append(f"无法读取参考标注文件 {path}：{exc}")
            continue
        reference_hashes.update(item for item in hashes if item)
        reference_groups.update(groups)

    if not metadata_path.is_file():
        report.errors.append(f"标注文件不存在：{metadata_path}")
        return report

    try:
        lines = metadata_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        report.errors.append(f"无法读取标注文件：{exc}")
        return report

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            report.warnings.append(f"第 {line_number} 行为空，已跳过。")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            report.errors.append(f"第 {line_number} 行不是合法 JSON：{exc.msg}")
            continue
        if not isinstance(record, dict):
            report.errors.append(f"第 {line_number} 行必须是 JSON 对象。")
            continue

        sample_id = record.get("sample_id")
        group_id = record.get("group_id")
        file_name = record.get("file_name") or record.get("image")
        question = record.get("question")
        answer = record.get("answer") or record.get("text")

        if not isinstance(sample_id, str) or not sample_id.strip():
            report.errors.append(f"第 {line_number} 行缺少 sample_id。")
        elif sample_id in seen_sample_ids:
            report.errors.append(f"第 {line_number} 行 sample_id 重复：{sample_id}")
        else:
            seen_sample_ids.add(sample_id)

        if not isinstance(group_id, str) or not group_id.strip():
            report.errors.append(f"第 {line_number} 行缺少 group_id。")
        else:
            report.group_ids.add(group_id)
            if group_id in reference_groups:
                report.errors.append(
                    f"第 {line_number} 行 group_id 与参考数据集重叠：{group_id}"
                )

        if not isinstance(file_name, str) or not file_name.strip():
            report.errors.append(f"第 {line_number} 行缺少 file_name。")
        else:
            if not is_safe_relative_path(file_name) or not file_name.startswith("images/"):
                report.errors.append(
                    f"第 {line_number} 行图片路径必须是 images/ 下的安全相对路径：{file_name}"
                )
            else:
                if file_name in seen_file_names:
                    report.warnings.append(f"第 {line_number} 行重复引用图片：{file_name}")
                seen_file_names.add(file_name)

                image_path = (root / file_name).resolve()
                image_root = (root / "images").resolve()
                if not image_path.is_relative_to(image_root) or not image_path.is_file():
                    report.errors.append(f"第 {line_number} 行图片不存在：{file_name}")
                elif image_path.suffix.lower() not in IMAGE_EXTENSIONS:
                    report.errors.append(f"第 {line_number} 行图片格式不支持：{file_name}")
                elif not image_signature_ok(image_path):
                    report.errors.append(f"第 {line_number} 行图片签名无效：{file_name}")
                else:
                    image_hash = sha256_file(image_path)
                    report.image_hashes[file_name] = image_hash
                    if image_hash in reference_hashes:
                        report.errors.append(
                            f"第 {line_number} 行图片与参考数据集重复：{file_name}"
                        )
                    same_hash_groups[image_hash].add(str(group_id))

        if not isinstance(question, str) or not question.strip():
            report.errors.append(f"第 {line_number} 行 question 不能为空。")
        if not isinstance(answer, str) or not answer.strip():
            report.errors.append(f"第 {line_number} 行 answer/text 不能为空。")
        elif any(marker in answer for marker in PLACEHOLDER_MARKERS):
            report.errors.append(f"第 {line_number} 行 answer 仍是占位内容。")

        task_type = record.get("task_type")
        domain = record.get("domain")
        difficulty = record.get("difficulty")
        if not isinstance(task_type, str) or not task_type.strip():
            report.errors.append(f"第 {line_number} 行缺少 task_type。")
        else:
            report.task_types[task_type] += 1
        if isinstance(domain, str) and domain.strip():
            report.domains[domain] += 1
        else:
            report.warnings.append(f"第 {line_number} 行未填写 domain。")
        if isinstance(difficulty, str) and difficulty.strip():
            report.difficulties[difficulty] += 1
        else:
            report.warnings.append(f"第 {line_number} 行未填写 difficulty。")

        review_status = record.get("review_status", "unreviewed")
        if review_status in {"reviewed", "double_checked", "source_annotated"}:
            report.reviewed_samples += 1
        elif allow_unreviewed:
            report.warnings.append(f"第 {line_number} 行尚未复核。")
        else:
            report.errors.append(f"第 {line_number} 行尚未完成标注复核。")

        validate_json_answer(record, line_number, report)
        report.records.append(record)

    for image_hash, groups in same_hash_groups.items():
        if len(groups) > 1:
            report.errors.append(
                f"同一图片哈希对应多个 group_id，疑似数据分组错误：{image_hash[:12]}"
            )

    return report


def build_manifest(
    root: Path, metadata_path: Path, report: ValidationReport
) -> dict[str, Any]:
    metadata_hash = sha256_file(metadata_path)
    image_hashes = sorted(report.image_hashes.values())
    digest = hashlib.sha256()
    digest.update(metadata_hash.encode("ascii"))
    for image_hash in image_hashes:
        digest.update(image_hash.encode("ascii"))
    sources = sorted(
        {
            str(record.get("source"))
            for record in report.records
            if isinstance(record.get("source"), str) and record.get("source")
        }
    )
    licenses = sorted(
        {
            str(record.get("license"))
            for record in report.records
            if isinstance(record.get("license"), str) and record.get("license")
        }
    )
    return {
        "dataset_id": "vision_understanding_eval",
        "version": "v1",
        "state": "ready" if report.ok else "failed",
        "purpose": "独立视觉理解最终评测",
        "frozen": False,
        "source": {
            "names": sources,
            "licenses": licenses,
            "source_urls": sorted(
                {
                    str(record.get("source_url"))
                    for record in report.records
                    if isinstance(record.get("source_url"), str)
                    and record.get("source_url")
                }
            ),
        },
        "policy": {
            "used_for_training": False,
            "used_for_validation": False,
            "used_for_hyperparameter_selection": False,
            "used_for_final_evaluation": True,
        },
        "split_policy": {
            "strategy": "independent_golden_set",
            "group_field": "group_id",
            "stratify_fields": ["task_type", "domain", "difficulty"],
            "seed": None,
        },
        "counts": {
            "samples": len(report.records),
            "images": len(report.image_hashes),
            "groups": len(report.group_ids),
            "reviewed_samples": report.reviewed_samples,
            "task_types": dict(report.task_types),
            "domains": dict(report.domains),
            "difficulties": dict(report.difficulties),
        },
        "quality": {
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "unreviewed_samples": len(report.records) - report.reviewed_samples,
        },
        "files": {
            "metadata": metadata_path.relative_to(root).as_posix(),
            "images_root": "images/",
            "metadata_sha256": metadata_hash,
            "dataset_sha256": digest.hexdigest(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验视觉理解黄金测试集并生成 manifest。")
    parser.add_argument("root", type=Path, help="测试集根目录")
    parser.add_argument("--metadata", default="metadata.jsonl", help="标注文件名")
    parser.add_argument(
        "--reference-metadata",
        action="append",
        type=Path,
        default=[],
        help="可重复传入训练集或验证集 metadata.jsonl，用于检查图片和 group_id 泄漏",
    )
    parser.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="允许未完成复核的样本，仅用于整理阶段",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="校验后写入根目录 manifest.json",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    metadata_path = (root / args.metadata).resolve()
    if not metadata_path.is_relative_to(root):
        parser.error("--metadata 必须位于测试集根目录内")
    report = validate_dataset(
        root,
        metadata_path,
        reference_metadata=[path.resolve() for path in args.reference_metadata],
        allow_unreviewed=args.allow_unreviewed,
    )
    print(f"样本数：{len(report.records)}")
    print(f"图片数：{len(report.image_hashes)}")
    print(f"分组数：{len(report.group_ids)}")
    print(f"已复核：{report.reviewed_samples}")
    print(f"错误：{len(report.errors)}，警告：{len(report.warnings)}")
    for message in report.errors:
        print(f"[错误] {message}")
    for message in report.warnings:
        print(f"[警告] {message}")

    if args.write_manifest:
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                build_manifest(root, metadata_path, report),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"已写入：{manifest_path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
