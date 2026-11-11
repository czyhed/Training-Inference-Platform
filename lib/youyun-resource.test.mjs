import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const componentPath = new URL("../components/vision-training-module.tsx", import.meta.url);

test("visual training sends only Youyun resource selection fields", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /function youyunSelectionPayload\(/);
  assert.match(source, /youyunResource: form\.provider === "youyun" \? youyunSelectionPayload\(\) : undefined/);
  assert.match(source, /优云训练环境镜像/);
  assert.match(source, /预计按量费用/);
  assert.doesNotMatch(source, /youyunResource: form\.provider === "youyun" \? youyunResource : undefined/);
});
