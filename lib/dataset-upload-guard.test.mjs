import assert from "node:assert/strict";
import test from "node:test";

import { validateDatasetUploadFiles } from "./dataset-upload-guard.ts";

function file(name, size = 100) {
  const payload = new ArrayBuffer(Math.min(size, 64));
  const result = new File([payload], name);
  if (result.size !== size) {
    Object.defineProperty(result, "size", { value: size });
  }
  return result;
}

test("视觉理解 allows a single ZIP archive", () => {
  const error = validateDatasetUploadFiles("视觉理解", "training", [file("bundle.zip")]);

  assert.equal(error, "");
});

test("视觉理解 rejects non-ZIP files", () => {
  const error = validateDatasetUploadFiles("视觉理解", "training", [file("notes.jsonl")]);

  assert.match(error, /格式不支持/);
  assert.match(error, /zip/);
});

test("视觉理解 rejects archives over 2GB", () => {
  const oversized = file("bundle.zip", 2 * 1024 * 1024 * 1024 + 1);

  const error = validateDatasetUploadFiles("视觉理解", "training", [oversized]);

  assert.match(error, /视觉理解数据包不能超过 2GB/);
});

test("文本生成 training still only accepts JSONL", () => {
  const error = validateDatasetUploadFiles("文本生成", "training", [file("bundle.zip")]);

  assert.match(error, /格式不支持/);
  assert.match(error, /jsonl/);
});

test("文本生成 training rejects files over 2GB", () => {
  const error = validateDatasetUploadFiles("文本生成", "training", [
    file("train.jsonl", 2 * 1024 * 1024 * 1024 + 1),
  ]);

  assert.match(error, /单个数据文件不能超过 2GB/);
});

test("文生图 accepts whitelisted images and enforces count and size", () => {
  assert.equal(validateDatasetUploadFiles("文生图", "training", [file("a.png"), file("b.JPG")]), "");
  assert.match(
    validateDatasetUploadFiles("文生图", "training", [file("a.jsonl")]),
    /格式不支持/,
  );
  assert.match(
    validateDatasetUploadFiles("文生图", "training", Array.from({ length: 201 }, (_, index) => file(`img-${index}.png`))),
    /最多上传 200 张图片/,
  );
  assert.match(
    validateDatasetUploadFiles("文生图", "training", [file("big.png", 21 * 1024 * 1024)]),
    /超过 20MB/,
  );
});

test("evaluation purpose keeps JSONL-only with 10-file and 200MB limits", () => {
  assert.equal(validateDatasetUploadFiles("文本生成", "evaluation", [file("eval.jsonl")]), "");
  assert.match(
    validateDatasetUploadFiles("文本生成", "evaluation", Array.from({ length: 11 }, (_, index) => file(`eval-${index}.jsonl`))),
    /最多上传 10 个 JSONL 文件/,
  );
  assert.match(
    validateDatasetUploadFiles("文本生成", "evaluation", [file("eval.jsonl", 201 * 1024 * 1024)]),
    /评测集单个文件不能超过 200MB/,
  );
});
