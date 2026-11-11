import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("vision model management view separates assets from the model catalog", async () => {
  const source = await readFile(
    new URL("../components/vision-model-management-view.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /本地训练/);
  assert.match(source, /优云训练/);
  assert.match(source, /推理部署/);
  assert.doesNotMatch(source, /vision-model-status-strip/);
  assert.doesNotMatch(source, /需要补充配置/);
  assert.match(source, /className="vision-model-catalog-drawer"/);
  assert.match(source, /浏览基模目录/);
  assert.match(source, /className="vision-model-mobile-list"/);
  assert.doesNotMatch(source, /vision-youyun-recommendation-list/);
});

test("vision model management shows Youyun catalog paths and VRAM guidance", async () => {
  const source = await readFile(new URL("../components/vision-model-management-view.tsx", import.meta.url), "utf8");
  assert.match(source, /remotePath/);
  assert.match(source, /recommendedVramGb/);
  assert.match(source, /优云挂载路径/);
});

test("vision model registration is a compact confirmation form", async () => {
  const source = await readFile(
    new URL("../components/vision-model-management-view.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /已选基模/);
  assert.match(source, /自定义模型/);
  assert.match(source, /登记并检查/);
  assert.doesNotMatch(source, /vision-model-preset-list/);
});

test("vision model management lands on the youyun public model library view", async () => {
  const source = await readFile(
    new URL("../components/vision-model-management-view.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /useState<ModelAssetTab>\("catalog"\)/);
  assert.match(source, /优云公共模型库/);
  assert.match(source, /搜索模型名称或公共模型 ID/);
  assert.match(source, /容器内路径/);
  assert.match(source, /catalogModels/);
  assert.match(source, /大小未知/);
  assert.match(source, /优云未提供大小/);
  assert.match(source, /usedForTraining|用于训练/);
});

test("vision module loads the live youyun catalog for model management", async () => {
  const source = await readFile(
    new URL("../components/vision-understanding-module.tsx", import.meta.url),
    "utf8",
  );
  assert.match(source, /\/vision\/youyun-catalog/);
  assert.match(source, /catalogModels=\{youyunCatalog\}/);
});

test("vision asset table uses text link actions and fits the container", async () => {
  const [source, css] = await Promise.all([
    readFile(new URL("../components/vision-model-management-view.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  const actions = source.slice(source.indexOf("function AssetActions"), source.indexOf("export function VisionModelManagementView"));
  assert.doesNotMatch(actions, /icon-only-btn/);
  assert.match(actions, /重新探针/);
  assert.match(actions, /归档/);
  assert.match(actions, /恢复/);
  assert.match(actions, /详情/);
  assert.match(css, /\.unified-model-page \.vision-model-table \{ min-width: 0; width: 100%; \}/);
  assert.match(css, /\.unified-model-page \.vision-model-table td \{ height: 60px; min-height: 0; \}/);
});
