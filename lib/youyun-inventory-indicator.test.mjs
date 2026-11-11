import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cardModules = [
  "training-module.tsx",
  "inference-module.tsx",
  "image-generation-training.tsx",
  "image-generation-service.tsx",
  "vision-training-module.tsx",
  "vision-understanding-module.tsx",
  "vision-inference-service-module.tsx",
];

test("Youyun GPU cards use the compact inventory indicator", async () => {
  for (const moduleName of cardModules) {
    const source = await readFile(new URL(`../components/${moduleName}`, import.meta.url), "utf8");
    assert.match(source, /YouyunInventoryIndicator/);
  }
});

test("compact indicator exposes the full inventory reason to assistive text", async () => {
  const source = await readFile(new URL("../components/youyun-inventory-indicator.tsx", import.meta.url), "utf8");
  assert.match(source, /title=\{label\}/);
  assert.match(source, /aria-label=\{label\}/);
  assert.match(source, /<i aria-hidden="true" \/>/);
});

test("training resource changes clear the previous submit error", async () => {
  const source = await readFile(new URL("../components/training-module.tsx", import.meta.url), "utf8");
  assert.match(source, /if \(environment === "youyun"\) setFormError\(""\)/);
  assert.match(source, /selectedYouyunCpuCores, selectedYouyunMemoryGb/);
});
