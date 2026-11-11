import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const component = new URL("../components/image-generation-tasks.tsx", import.meta.url);
const service = new URL("../components/image-generation-service.tsx", import.meta.url);
const page = new URL("../app/page.tsx", import.meta.url);

test("image generation tasks submit only to a running service", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /\/image-generation\/tasks/);
  assert.match(source, /创建任务/);
  assert.match(source, /提交生成/);
  assert.match(source, /不会创建新的优云实例/);
  assert.match(source, /negativePrompt/);
  assert.match(source, /batchSize/);
});

test("image generation task form keeps stopped service guidance beside the submit action", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /onOpenServiceOverview/);
  assert.match(source, /当前服务未运行，暂时不能创建生图任务/);
  assert.match(source, /返回服务概览启动/);
  assert.match(source, /const serviceRunning = serviceState === "running"/);
});

test("image generation tasks expose timeline results stop and retry", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /awaiting_confirmation/);
  assert.match(source, /collecting/);
  assert.match(source, /results\/\$\{result\.id\}/);
  assert.match(source, /act\("stop"\)/);
  assert.match(source, /act\("retry"\)/);
  assert.match(source, /重试清理实例/);
  assert.match(source, /下载原图/);
});

test("image generation tasks show preflight status before paid confirmation", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /启动前预检/);
  assert.match(source, /preflight/);
  assert.match(source, /预检未通过/);
  assert.match(source, /expiresAtEpoch/);
});

test("image generation tasks render safe runtime diagnostics", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /diagnostics/);
  assert.match(source, /failureCode/);
  assert.match(source, /failureMessage/);
  assert.match(source, /lastHeartbeatAt/);
  assert.doesNotMatch(source, /remoteCommand/);
});

test("image generation tasks expose history filters and lifecycle actions", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /includeArchived/);
  assert.match(source, /serviceId/);
  assert.match(source, /query/);
  assert.match(source, /tasks\/\$\{[^}]+\}\/clone/);
  assert.match(source, /tasks\/\$\{[^}]+\}\/archive/);
  assert.match(source, /tasks\/\$\{[^}]+\}\/restore/);
  assert.match(source, /method:\s*["']DELETE["']/);
  assert.match(source, /复制并重新生成|重新生成/);
  assert.match(source, /已归档/);
  assert.doesNotMatch(source, /resultRoot/);
  assert.doesNotMatch(source, /remoteHandle/);
});

test("image generation service renders read-only result history", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /image-generation\/services\/\$\{savedServiceId\}\/results/);
  assert.match(source, /生成历史/);
  assert.match(source, /taskId/);
  assert.match(source, /available/);
  assert.match(source, /下载|Download/);
  assert.doesNotMatch(source, /resultRoot/);
  assert.doesNotMatch(source, /remoteHandle/);
});
