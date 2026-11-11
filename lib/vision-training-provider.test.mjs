import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const componentPath = new URL("../components/vision-understanding-module.tsx", import.meta.url);

test("visual training keeps Youyun configuration hidden until remote environment is selected", async () => {
  const source = await readFile(componentPath, "utf8");

  assert.match(source, /provider:\s*"local"/);
  assert.match(source, /trainingForm\.provider === "youyun" && <div className="youyun-specs-panel vision-youyun-specs-panel">/);
  assert.match(source, /trainingForm\.provider === "youyun" \? youyunSelectionPayload\(\) : undefined/);
});

test("visual training carries the selected SFT or DPO type through preflight and submission", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /taskType:\s*form\.taskType/);
  assert.match(source, /dpoBeta:\s*Number\(form\.dpoBeta\)/);
  assert.match(source, /训练类型/);
  assert.match(source, /视觉理解 IMAGE_TEXT \{form\.taskType\}/);
});

test("visual training requests models from the selected execution environment", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /vision\/models\?\$\{modelQuery\.toString\(\)\}/);
  assert.match(source, /new URLSearchParams\(\{ provider: form\.provider \}\)/);
  assert.match(source, /公共模型库/);
});

test("visual training only describes public mounted Youyun models", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /downloadFallback/);
  assert.doesNotMatch(source, /ModelScope 下载/);
  assert.doesNotMatch(source, /YOUYUN_ALLOW_MODEL_DOWNLOAD/);
  assert.match(source, /优云公共模型库/);
});

test("visual training keeps remote candidate models selectable so preflight can explain blockers", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /modelPickerModels/);
  assert.match(source, /modelPickerModels\.some/);
  assert.match(source, /disabled=\{form\.provider === "local" && !canUse\}/);
  assert.match(source, /!modelOptions\.length/);
});

test("visual training exposes one sticky action bar and grouped preflight results", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /className="training-form-footer"/);
  assert.match(source, /className="vision-preflight-grid"/);
  assert.match(source, /当前阻塞问题/);
  assert.doesNotMatch(source, /className="vision-create-actions"/);
});

test("visual task details expose a stage timeline and filtered log controls", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /className="vision-stage-timeline"/);
  assert.match(source, /className="vision-log-controls"/);
  assert.match(source, /只看错误/);
  assert.match(source, /自动滚动/);
});

test("visual task details keep polling until remote cleanup is confirmed", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /taskNeedsPolling/);
  assert.match(source, /cleanupState === "releasing"/);
  assert.match(source, /resourceState === "cleanup_failed"/);
});

test("visual training makes validated but unpublished datasets actionable", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");
  const pageSource = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.match(source, /readyVisualDatasets/);
  assert.match(source, /发布并选择/);
  assert.match(source, /datasets\/\$\{dataset\.id\}\/publish/);
  assert.match(source, /onLoadDatasets/);
  assert.match(source, /onOpenDatasets/);
  assert.match(pageSource, /<VisionTrainingModule[\s\S]*onLoadDatasets=\{refreshDatasets\}[\s\S]*onOpenDatasets=/);
});

test("visual training detail exposes remote resource cleanup and failure guidance", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /cleanupState\?:/);
  assert.match(source, /resourceState\?:/);
  assert.match(source, /failureCode\?:/);
  assert.match(source, /phase\?:/);
  assert.match(source, /suggestedAction\?:/);
  assert.match(source, /清理状态/);
  assert.match(source, /失败分类/);
  assert.match(source, /失败阶段/);
  assert.match(source, /建议动作/);
});

test("visual training requires explicit confirmation for a paid remote probe", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /models\/\$\{form\.modelId\}\/remote-probe/);
  assert.match(source, /创建临时实例并探针/);
  assert.match(source, /探针结束后自动释放/);
  assert.match(source, /cleanupState/);
});

test("visual training shows community environments separately from live Youyun images", async () => {
  const trainingSource = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");
  const workbenchSource = await readFile(componentPath, "utf8");

  for (const source of [trainingSource, workbenchSource]) {
    assert.match(source, /communityImages/);
    assert.match(source, /社区训练环境/);
    assert.match(source, /当前区域无库存\/未上架/);
  }
  assert.match(trainingSource, /已匹配，待验证/);
  assert.match(workbenchSource, /已发现，待验证/);
  assert.match(trainingSource, /selectedCommunityImage/);
  assert.match(trainingSource, /验证此环境/);
});

test("visual training filters community images by compatibility and automatically validates ready configuration", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /compatibleCommunityImages/);
  assert.match(source, /image\.compatible !== false/);
  assert.match(source, /自动校验/);
  assert.match(source, /runAutomaticPreflight/);
  assert.match(source, /推荐理由/);
});

test("visual training no longer exposes online model download fallback", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(source, /downloadFallback/);
  assert.doesNotMatch(source, /downloadAllowed/);
  assert.doesNotMatch(source, /ModelScope 下载/);
  assert.doesNotMatch(source, /YOUYUN_ALLOW_MODEL_DOWNLOAD/);
});

test("visual training exposes Pod region selection and persists the selected location", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");

  assert.match(source, /youyun-location/);
  assert.match(source, /instanceMode:\s*"pod"/);
  assert.match(source, /zones:\s*\{/);
  assert.match(source, /selectedYouyunRegion/);
  assert.match(source, /selectedYouyunZone/);
  assert.match(source, /className="youyun-region-list"/);
  assert.match(source, /selectYouyunZone/);
  assert.match(source, /region:\s*selectedYouyunRegion/);
  assert.match(source, /zone:\s*selectedYouyunZone/);
});

test("vision training list uses fixed layout so it never overflows", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.vision-training-page \.vision-training-table table \{ table-layout: fixed; min-width: 0; width: 100%; \}/);
  assert.match(css, /\.vision-training-page \.vision-training-table td \{ height: 60px; min-height: 0;/);
  assert.match(css, /\.vision-training-page \.vision-training-table \.row-sub \{ max-width: 100%; margin-top: 2px; \}/);
  assert.match(css, /\.vision-training-page \.vision-training-table \.dataset-link \{ display: inline-block; max-width: 100%; overflow: hidden;/);
});
