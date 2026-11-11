from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import zipfile
from pathlib import Path

import requests


DESKTOP = Path.home() / "Desktop"
OUTPUT_ROOT = DESKTOP / "cppt_vision_understanding_datasets_v1"
API_URL = "https://api.openverse.org/v1/images/"
USER_AGENT = "CPPT-Platform-vision-dataset-builder/1.0"
HEADERS = {"User-Agent": USER_AGENT, "Connection": "close"}

SUBJECTS = [
    ("cat", "猫"),
    ("dog", "狗"),
    ("bird", "鸟"),
    ("flower", "花朵"),
    ("mountain", "山景"),
    ("beach", "海滩"),
    ("bicycle", "自行车"),
    ("car", "汽车"),
    ("fruit", "水果"),
    ("building", "建筑物"),
]


def request(url: str, timeout: int) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 5:
                time.sleep(attempt * 3)
    raise RuntimeError(f"下载失败，重试 5 次后仍未成功：{url}\n{last_error}") from last_error


def request_json(url: str) -> dict:
    return request(url, 60).json()


def download(url: str, target: Path) -> str:
    response = request(url, 90)
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    data = response.content
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError(f"不是受支持的图片类型：{content_type}")
    if len(data) == 0 or len(data) > 20 * 1024 * 1024:
        raise ValueError("图片为空或超过 20MB。")
    if content_type == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("JPEG 文件签名校验失败。")
    if content_type == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG 文件签名校验失败。")
    if content_type == "image/webp" and not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        raise ValueError("WebP 文件签名校验失败。")
    extension = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[content_type]
    final_target = target.with_suffix(extension)
    final_target.write_bytes(data)
    return final_target.name


def collect_assets() -> list[dict]:
    assets: list[dict] = []
    seen_urls: set[str] = set()
    for query, label in SUBJECTS:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "license": "cc0",
                "source": "flickr",
                "mature": "false",
                "per_page": 30,
            }
        )
        payload = request_json(f"{API_URL}?{params}")
        accepted = 0
        for item in payload.get("results", []):
            url = str(item.get("url") or "")
            if not url or url in seen_urls or str(item.get("license") or "").lower() != "cc0":
                continue
            if not str(item.get("creator") or "").strip():
                continue
            assets.append(
                {
                    "query": query,
                    "label": label,
                    "title": str(item.get("title") or query),
                    "url": url,
                    "creator": str(item.get("creator") or "未知作者"),
                    "license": str(item.get("license") or "cc0").upper(),
                    "licenseVersion": str(item.get("license_version") or "1.0"),
                    "source": str(item.get("foreign_landing_url") or url),
                }
            )
            seen_urls.add(url)
            accepted += 1
            if accepted >= 3:
                break
        if accepted < 3:
            raise RuntimeError(f"主题 {query} 未找到足够的 CC0 图片，仅找到 {accepted} 张。")
    return assets


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def make_zip(source_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir).as_posix())


def build_split(name: str, rows: list[dict], all_assets: list[dict]) -> None:
    split_dir = OUTPUT_ROOT / name
    if split_dir.exists():
        shutil.rmtree(split_dir)
    image_dir = split_dir / "images"
    image_dir.mkdir(parents=True)
    metadata: list[dict] = []
    sources: list[dict] = []

    for index, asset in enumerate(rows, 1):
        stem = f"{name}_{index:03d}"
        filename = download(asset["url"], image_dir / stem)
        image_path = image_dir / filename
        metadata.append(
            {
                "image": f"images/{filename}",
                "question": "图中主要是什么？",
                "answer": asset["label"],
                "source_title": asset["title"],
                "source_url": asset["source"],
                "creator": asset["creator"],
                "license": asset["license"],
                "license_version": asset["licenseVersion"],
            }
        )
        sources.append(
            {
                "file_name": filename,
                "query": asset["query"],
                "title": asset["title"],
                "creator": asset["creator"],
                "license": asset["license"],
                "license_version": asset["licenseVersion"],
                "source_url": asset["source"],
                "download_url": asset["url"],
                "size_bytes": image_path.stat().st_size,
            }
        )

    write_jsonl(split_dir / "metadata.jsonl", metadata)
    write_jsonl(split_dir / "sources.jsonl", sources)
    manifest = {
        "datasetName": f"CPPT 视觉理解 {name} v1",
        "split": name,
        "sampleCount": len(metadata),
        "imageCount": len(metadata),
        "licensePolicy": "Openverse API returned CC0; source metadata retained for verification.",
        "source": "Openverse",
        "subjects": sorted({item["answer"] for item in metadata}),
        "files": {"metadata": "metadata.jsonl", "images": "images/", "sources": "sources.jsonl"},
    }
    (split_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (split_dir / "README.md").write_text(
        f"""# CPPT 视觉理解{name}集 v1

样本数：{len(metadata)}

目录结构：

```text
{name}/
├─ metadata.jsonl
├─ sources.jsonl
├─ manifest.json
└─ images/
```

`metadata.jsonl` 可直接用于 CPPT 平台的视觉理解 ZIP 上传。图片由 Openverse 检索，
本批次仅保留 API 返回 `CC0` 的结果；每条样本的来源页、下载地址、作者和许可信息保存在
`metadata.jsonl` 与 `sources.jsonl` 中。

注意：Openverse 的许可字段应作为检索依据，正式商业发布前仍应打开每条 `source_url`
核对原始页面的许可和人物、商标等其他权利状态。
""",
        encoding="utf-8",
    )
    make_zip(split_dir, OUTPUT_ROOT / f"{name}.zip")


def main() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)
    assets = collect_assets()
    splits = [("train", assets[:18]), ("validation", assets[18:24]), ("test", assets[24:30])]
    for name, rows in splits:
        build_split(name, rows, assets)
    summary = {
        "root": str(OUTPUT_ROOT),
        "train": str(OUTPUT_ROOT / "train.zip"),
        "validation": str(OUTPUT_ROOT / "validation.zip"),
        "test": str(OUTPUT_ROOT / "test.zip"),
        "total": len(assets),
    }
    (OUTPUT_ROOT / "README.md").write_text(
        "# CPPT 视觉理解训练/验证/测试数据集 v1\n\n"
        "本目录包含 train、validation、test 三份独立数据集及对应 ZIP。\n\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
