import assert from "node:assert/strict";
import test from "node:test";

import {
  visionModelBucket,
  visionModelCounts,
  visionModelMetadata,
  visionModelReadiness,
} from "./vision-model-management.ts";

const baseModel = {
  id: "vision-model",
  name: "Qwen VL",
  repoId: "Qwen/Qwen2.5-VL-3B-Instruct",
  revision: "main",
  path: "",
  capabilities: [
    "vision_language",
    "image_understanding",
    "lora_training",
    "qlora_training",
    "transformers_serving",
  ],
  supported: false,
  probeStatus: "candidate",
  probeMessage: "仓库候选",
  availableOnYouyun: false,
};

test("vision model management identifies catalog metadata conservatively", () => {
  assert.deepEqual(
    visionModelMetadata({
      ...baseModel,
      path: "/models/qwen",
      supported: true,
      probeStatus: "passed",
    }),
    { family: "Qwen2.5-VL", parameterScale: "3B" },
  );
  assert.deepEqual(
    visionModelMetadata({
      ...baseModel,
      repoId: "custom/acme-vision",
      name: "Acme Vision",
    }),
    { family: "自定义", parameterScale: "其他" },
  );
});

test("vision model management derives three workflow exits from one rule set", () => {
  const localPassed = {
    ...baseModel,
    path: "/models/qwen",
    supported: true,
    probeStatus: "passed",
  };
  assert.deepEqual(visionModelReadiness(localPassed), {
    localTraining: { state: "ready", reason: "本地目录和训练能力已通过检查" },
    youyunTraining: { state: "pending", reason: "Repo ID 尚未配置到优云可用模型目录" },
    inference: { state: "ready", reason: "本地目录和推理能力已通过检查" },
  });

  const remoteCandidate = {
    ...baseModel,
    availableOnYouyun: true,
  };
  assert.deepEqual(visionModelReadiness(remoteCandidate), {
    localTraining: { state: "pending", reason: "需要配置服务器可访问的本地模型目录" },
    youyunTraining: { state: "ready", reason: "Repo ID 已配置到优云可用模型目录" },
    inference: { state: "pending", reason: "需要配置服务器可访问的本地模型目录" },
  });
  assert.equal(visionModelBucket(remoteCandidate), "available");

  const missingPathModel = {
    ...baseModel,
    path: "/missing/model",
    probeStatus: "missing",
    probeMessage: "模型路径不存在",
  };
  assert.equal(visionModelBucket(missingPathModel), "error");
});

test("vision model management aggregates each asset once", () => {
  const localPassed = {
    ...baseModel,
    id: "local",
    path: "/models/qwen",
    supported: true,
    probeStatus: "passed",
  };
  const remoteCandidate = {
    ...baseModel,
    id: "remote",
    repoId: "Qwen/Qwen2-VL-7B-Instruct",
    availableOnYouyun: true,
  };
  const failed = {
    ...baseModel,
    id: "failed",
    repoId: "custom/not-vision",
    capabilities: [],
    probeStatus: "unsupported",
  };

  assert.deepEqual(visionModelCounts([localPassed, remoteCandidate, failed]), {
    total: 3,
    localReady: 1,
    youyunReady: 1,
    attention: 3,
  });
});
