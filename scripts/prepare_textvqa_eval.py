from __future__ import annotations

import argparse
import json
import random
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


ANNOTATIONS_URL = "https://dl.fbaipublicfiles.com/textvqa/data/TextVQA_0.5.1_val.json"
USER_AGENT = "cppt-platform-open-eval-preparer/1.0"


def request_bytes(url: str, *, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def image_kind(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def majority_answer(answers: list[str]) -> str:
    normalized = [answer.strip() for answer in answers if answer.strip()]
    if not normalized:
        return ""
    return Counter(normalized).most_common(1)[0][0]


def prepare(root: Path, sample_size: int, seed: int, overwrite: bool) -> int:
    images_root = root / "images"
    images_root.mkdir(parents=True, exist_ok=True)
    metadata_path = root / "metadata.jsonl"
    manifest_path = root / "manifest.json"

    if not overwrite and (metadata_path.exists() or manifest_path.exists()):
        raise RuntimeError(
            f"{root} 已存在正式测试集文件。使用 --overwrite 才会重新生成。"
        )

    raw = json.loads(request_bytes(ANNOTATIONS_URL).decode("utf-8"))
    records = raw.get("data", [])
    if not isinstance(records, list):
        raise RuntimeError("TextVQA 标注文件结构不符合预期。")

    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        image_id = str(record.get("image_id", "")).strip()
        if image_id and image_id not in unique:
            unique[image_id] = record

    candidates = list(unique.values())
    random.Random(seed).shuffle(candidates)
    selected: list[dict[str, Any]] = []
    downloaded: list[dict[str, Any]] = []

    for record in candidates:
        if len(selected) >= sample_size:
            break
        image_id = str(record.get("image_id", "")).strip()
        urls = [
            record.get("flickr_300k_url"),
            record.get("flickr_original_url"),
        ]
        image_data = None
        source_url = None
        for url in urls:
            if not isinstance(url, str) or not url:
                continue
            try:
                candidate = request_bytes(url, timeout=12)
            except Exception:
                continue
            if image_kind(candidate):
                image_data = candidate
                source_url = url
                break
        if image_data is None or source_url is None:
            continue

        kind = image_kind(image_data)
        assert kind is not None
        file_name = f"images/textvqa-{len(selected) + 1:04d}.{kind}"
        (root / file_name).write_bytes(image_data)
        item = dict(record)
        item["_local_file_name"] = file_name
        item["_source_url"] = source_url
        selected.append(item)
        downloaded.append(
            {
                "image_id": image_id,
                "file_name": file_name,
                "source_url": source_url,
                "bytes": len(image_data),
            }
        )
        time.sleep(0.05)

    if len(selected) < sample_size:
        raise RuntimeError(
            f"只下载到 {len(selected)} 张图片，目标为 {sample_size}。"
        )

    output_rows: list[dict[str, Any]] = []
    for index, record in enumerate(selected, 1):
        answers = [
            answer
            for answer in record.get("answers", [])
            if isinstance(answer, str)
        ]
        output_rows.append(
            {
                "sample_id": f"textvqa-val-{index:04d}",
                "group_id": f"textvqa-image-{record['image_id']}",
                "file_name": record["_local_file_name"],
                "task_type": "ocr",
                "domain": "自然场景文字",
                "difficulty": "medium",
                "question": record["question"],
                "answer": majority_answer(answers),
                "answers": answers,
                "expected_format": "text",
                "tags": ["TextVQA", "OCR", "公开验证集"],
                "source": "TextVQA validation",
                "source_image_id": record["image_id"],
                "source_url": record["_source_url"],
                "license": "CC BY 4.0",
                "review_status": "source_annotated",
                "reviewer": "TextVQA human annotations",
                "notes": "从官方 TextVQA validation annotations 中按固定 seed 抽取；未用于本项目训练。",
            }
        )

    metadata_path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            for row in output_rows
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "dataset_id": "vision_understanding_eval_textvqa",
        "version": "v1-mini30",
        "state": "ready",
        "purpose": "视觉理解 OCR/VQA 独立最终评测",
        "frozen": False,
        "source": {
            "name": "TextVQA validation",
            "annotations_url": ANNOTATIONS_URL,
            "license": "CC BY 4.0",
            "source_dataset": "OpenImages",
            "sample_seed": seed,
            "sample_size": sample_size,
        },
        "policy": {
            "used_for_training": False,
            "used_for_validation": False,
            "used_for_hyperparameter_selection": False,
            "used_for_final_evaluation": True,
        },
        "counts": {
            "samples": len(output_rows),
            "images": len(downloaded),
            "task_types": {"ocr": len(output_rows)},
        },
        "files": {
            "metadata": "metadata.jsonl",
            "images_root": "images/",
            "source_images": downloaded,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(output_rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("data/vision_understanding_eval_v1"),
    )
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    count = prepare(
        args.root.resolve(),
        sample_size=max(1, args.sample_size),
        seed=args.seed,
        overwrite=args.overwrite,
    )
    print(f"已整理 TextVQA 测试集：{count} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
