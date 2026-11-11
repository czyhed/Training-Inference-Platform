import assert from "node:assert/strict";
import test from "node:test";
import { strToU8, zipSync } from "fflate";
import {
  parseImageGenerationArchive,
  validateImageGenerationUploadSelection,
} from "./image-generation-upload.ts";

function archiveFile(name, entries) {
  const bytes = zipSync(entries);
  return new File([bytes], name, { type: "application/zip", lastModified: 1 });
}

test("ZIP metadata matches image names case-insensitively and reports coverage", async () => {
  const archive = archiveFile("products.zip", {
    "images/Product.JPG": new Uint8Array([0xff, 0xd8, 0xff, 0xd9]),
    "metadata.jsonl": strToU8('{"file_name":"images/product.jpg","caption":"红色运动鞋"}\n'),
  });

  const parsed = await parseImageGenerationArchive(archive);

  assert.equal(parsed.files.length, 1);
  assert.deepEqual(parsed.captions.get("product.jpg"), { value: "红色运动鞋", explicit: true });
  assert.deepEqual(parsed.summary, {
    archiveName: "products.zip",
    imageCount: 1,
    metadataPresent: true,
    metadataRows: 1,
    mappedCaptionCount: 1,
    emptyCaptionCount: 0,
    autoFilledCount: 0,
  });
});

test("ZIP parsing preserves an explicitly empty Caption as a visible issue", async () => {
  const archive = archiveFile("empty-caption.zip", {
    "images/0001.png": new Uint8Array([0x89, 0x50, 0x4e, 0x47]),
    "metadata.jsonl": strToU8('{"file_name":"images/0001.png","caption":""}\n'),
  });

  const parsed = await parseImageGenerationArchive(archive);

  assert.deepEqual(parsed.captions.get("0001.png"), { value: "", explicit: true });
  assert.equal(parsed.summary.emptyCaptionCount, 1);
  assert.equal(parsed.summary.autoFilledCount, 0);
});

test("ZIP parsing counts images without an explicit Caption as filename autofill", async () => {
  const archive = archiveFile("autofill.zip", {
    "images/0001.webp": new Uint8Array([0x52, 0x49, 0x46, 0x46]),
    "images/0002.webp": new Uint8Array([0x52, 0x49, 0x46, 0x46]),
    "metadata.jsonl": strToU8('{"file_name":"images/0001.webp"}\n'),
  });

  const parsed = await parseImageGenerationArchive(archive);

  assert.equal(parsed.captions.has("0001.webp"), false);
  assert.equal(parsed.summary.metadataRows, 1);
  assert.equal(parsed.summary.autoFilledCount, 2);
});

test("ZIP parsing reports invalid UTF-8 metadata with an actionable error", async () => {
  const archive = archiveFile("invalid-encoding.zip", {
    "images/0001.jpg": new Uint8Array([0xff, 0xd8, 0xff, 0xd9]),
    "metadata.jsonl": new Uint8Array([0xff, 0xfe, 0xfd]),
  });

  await assert.rejects(
    () => parseImageGenerationArchive(archive),
    /metadata\.jsonl 不是有效的 UTF-8 文本/,
  );
});

test("upload mode validation prevents mixing direct images and ZIP archives", () => {
  const image = new File([new Uint8Array([1])], "0001.jpg", { type: "image/jpeg" });
  const archive = new File([new Uint8Array([1])], "dataset.zip", { type: "application/zip" });

  assert.equal(validateImageGenerationUploadSelection("images", [image]), "");
  assert.match(validateImageGenerationUploadSelection("images", [archive]), /切换到 ZIP 数据包/);
  assert.match(validateImageGenerationUploadSelection("archive", [image]), /仅接受一个 ZIP/);
  assert.match(validateImageGenerationUploadSelection("archive", [archive, archive]), /仅接受一个 ZIP/);
  assert.equal(validateImageGenerationUploadSelection("archive", [archive]), "");
});
