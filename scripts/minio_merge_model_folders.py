"""Merge duplicate MinIO prefix folders, e.g. rag-assets/models/** -> rag-assets/model/**.

历史上有外部工具把产物上传到了 models/ 前缀，而平台统一使用 model/ 前缀。
该脚本把源前缀下的对象复制到目标前缀（同名同大小视为已存在），校验后删除源对象。

用法：
    python scripts/minio_merge_model_folders.py            # 预演（只列出计划，不改动）
    python scripts/minio_merge_model_folders.py --apply    # 执行合并
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from minio import Minio  # noqa: E402
from minio.commonconfig import CopySource  # noqa: E402

from backend.model_storage import ModelStorageConfig  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合并 MinIO 桶内重复前缀目录")
    parser.add_argument("--source", default="models/", help="源前缀（默认 models/）")
    parser.add_argument("--target", default="model/", help="目标前缀（默认 model/）")
    parser.add_argument("--apply", action="store_true", help="实际执行复制与删除；缺省为预演")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_prefix = args.source if args.source.endswith("/") else args.source + "/"
    target_prefix = args.target if args.target.endswith("/") else args.target + "/"
    config = ModelStorageConfig.from_env()
    config.validate()
    client = Minio(
        config.minio_endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=config.minio_secure,
    )
    bucket = config.minio_bucket

    objects = list(client.list_objects(bucket, prefix=source_prefix, recursive=True))
    if not objects:
        print(f"{bucket} 桶中 {source_prefix} 下没有对象，无需合并。")
        return 0

    copied: list[str] = []
    identical: list[str] = []
    conflicts: list[str] = []
    for obj in objects:
        target_key = target_prefix + obj.object_name[len(source_prefix):]
        try:
            existing = client.stat_object(bucket, target_key)
        except Exception:
            existing = None
        if existing is not None:
            if existing.size == obj.size and existing.etag == obj.etag:
                identical.append(obj.object_name)
                print(f"[已一致] {obj.object_name} -> {target_key}（{obj.size} 字节）")
            else:
                conflicts.append(obj.object_name)
                print(f"[冲突跳过] 目标已存在且内容不同：{obj.object_name} -> {target_key}")
            continue
        print(f"[{'复制' if args.apply else '预演复制'}] {obj.object_name} -> {target_key}（{obj.size} 字节）")
        if not args.apply:
            copied.append(obj.object_name)
            continue
        client.copy_object(bucket, target_key, CopySource(bucket, obj.object_name))
        verify = client.stat_object(bucket, target_key)
        if verify.size != obj.size:
            print(f"[校验失败] {target_key} 大小不一致，保留源对象。")
            conflicts.append(obj.object_name)
            continue
        copied.append(obj.object_name)

    if args.apply:
        for key in copied:
            client.remove_object(bucket, key)
        print(f"完成：复制并删除源对象 {len(copied)} 个，内容一致删除源对象 {len(identical)} 个。")
    else:
        print(f"预演完成：将复制 {len(copied)} 个，源已一致 {len(identical)} 个。确认无误后加 --apply 执行。")
    if conflicts:
        print(f"有 {len(conflicts)} 个冲突对象未处理，需要人工确认：")
        for key in conflicts:
            print(f"  - {key}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
