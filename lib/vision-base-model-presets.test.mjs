import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_VISION_BASE_MODEL_FORM,
  VISION_BASE_MODEL_PRESETS,
  applyVisionBaseModelPreset,
  visionBaseModelRecommendationState,
} from "./vision-base-model-presets.ts";

test("offers only the Qwen vision base models supported by visual training", () => {
  assert.deepEqual(
    VISION_BASE_MODEL_PRESETS.map((model) => model.repoId),
    [
      "Qwen/Qwen2.5-VL-3B-Instruct",
      "Qwen/Qwen2.5-VL-7B-Instruct",
      "Qwen/Qwen2-VL-2B-Instruct",
      "Qwen/Qwen2-VL-7B-Instruct",
      "Qwen/Qwen3-VL-2B-Instruct",
      "Qwen/Qwen3-VL-4B-Instruct",
      "Qwen/Qwen3-VL-8B-Instruct",
      "Qwen/Qwen2.5-VL-72B-Instruct",
      "Qwen/Qwen3-VL-32B-Thinking-FP8",
      "Qwen/Qwen3-Omni-30B-A3B-Instruct",
    ],
  );
  assert.equal(VISION_BASE_MODEL_PRESETS.filter((model) => model.recommended).length, 1);
  assert.equal(VISION_BASE_MODEL_PRESETS[0].recommended, true);
  assert.deepEqual(
    VISION_BASE_MODEL_PRESETS.map((model) => model.youyunAvailability),
    ["ready", "cache_required", "cache_required", "cache_required", "cache_required", "cache_required", "cache_required", "ready", "ready", "ready"],
  );
  assert.deepEqual(DEFAULT_VISION_BASE_MODEL_FORM, {
    name: "Qwen2.5-VL 3B Instruct",
    repoId: "Qwen/Qwen2.5-VL-3B-Instruct",
    revision: "main",
    path: "",
  });
});

test("marks a recommended Youyun model as registered when its repository already exists", () => {
  assert.equal(
    visionBaseModelRecommendationState(
      VISION_BASE_MODEL_PRESETS[0],
      ["qwen/qwen2.5-vl-3b-instruct"],
    ),
    "registered",
  );
  assert.equal(
    visionBaseModelRecommendationState(VISION_BASE_MODEL_PRESETS[1], []),
    "cache_required",
  );
});

test("fills repository fields from a preset without replacing the local model path", () => {
  const next = applyVisionBaseModelPreset(
    {
      name: "自定义名称",
      repoId: "custom/model",
      revision: "dev",
      path: "/data/models/existing",
    },
    VISION_BASE_MODEL_PRESETS[1],
  );

  assert.deepEqual(next, {
    name: "Qwen2.5-VL 7B Instruct",
    repoId: "Qwen/Qwen2.5-VL-7B-Instruct",
    revision: "main",
    path: "/data/models/existing",
  });
});
