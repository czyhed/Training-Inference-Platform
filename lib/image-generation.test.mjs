import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const page = new URL("../app/page.tsx", import.meta.url);
const component = new URL("../components/image-generation-module.tsx", import.meta.url);
const uploadHelper = new URL("./image-generation-upload.ts", import.meta.url);
const models = new URL("../components/image-generation-models.tsx", import.meta.url);
const service = new URL("../components/image-generation-service.tsx", import.meta.url);
const inferenceWorkspace = new URL("../components/image-generation-inference-workspace.tsx", import.meta.url);
const training = new URL("../components/image-generation-training.tsx", import.meta.url);
const evaluation = new URL("../components/image-generation-evaluation.tsx", import.meta.url);

test("workspace accepts common image formats and supports Caption editing", async () => {
  const [source, helperSource] = await Promise.all([readFile(component, "utf8"), readFile(uploadHelper, "utf8")]);
  assert.match(source, /image\/jpeg,image\/png,image\/webp/);
  assert.match(source, /jpg.*jpeg.*png.*webp/);
  assert.match(source, /\.zip/);
  assert.match(helperSource, /unzipSync/);
  assert.match(source, /metadata\.jsonl/);
  assert.match(source, /caption/);
  assert.match(source, /Caption/);
  assert.match(source, /content_base64/);
});

test("image-generation dataset creation explains direct upload and ZIP requirements", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /格式要求与示例/);
  assert.match(source, /图片直传/);
  assert.match(source, /ZIP 数据包/);
  assert.match(source, /正确示例/);
  assert.match(source, /错误示例/);
  assert.match(source, /路径安全/);
  assert.match(source, /metadata\.jsonl/);
});

test("image-generation upload shows file health and explicit processing stages", async () => {
  const source = await readFile(component, "utf8");
  assert.match(source, /image-generation-upload-summary/);
  assert.match(source, /有效图片/);
  assert.match(source, /异常项/);
  assert.match(source, /仅看异常/);
  assert.match(source, /本地解析/);
  assert.match(source, /上传中/);
  assert.match(source, /服务端校验/);
  assert.match(source, /校验结果/);
  assert.match(source, /request\.upload\.onprogress/);
  assert.match(source, /drag-active/);
  assert.doesNotMatch(source, /onDrop=\{\(event\) => \{ if \(!busy\) onDrop\(event\); \}\}/);
});

test("image-generation creation gives direct images and ZIP distinct complete workflows", async () => {
  const source = await readFile(component, "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /image-generation-upload-mode/);
  assert.match(source, /图片直传/);
  assert.match(source, /ZIP 数据包/);
  assert.match(source, /validateImageGenerationUploadSelection/);
  assert.match(source, /image-generation-archive-summary/);
  assert.match(source, /元数据覆盖/);
  assert.match(source, /自动补全/);
  assert.match(source, /按文件名补全空 Caption/);
  assert.match(source, /删除异常项/);
  assert.match(source, /if \(!nextRows\.length\) setArchiveSummary\(null\)/);
  assert.match(css, /\.image-generation-upload-mode/);
  assert.match(css, /\.image-generation-archive-summary/);
});

test("image-generation ZIP validation rejects unsafe and inconsistent archives", async () => {
  const source = await readFile(uploadHelper, "utf8");
  assert.match(source, /unsafeArchiveEntry/);
  assert.match(source, /ZIP 中存在不安全路径/);
  assert.match(source, /metadata\.jsonl 引用了不存在的图片/);
  assert.match(source, /ZIP 中存在重复图片文件名/);
});

test("image-generation dataset creation uses the shared back control and responsive layout", async () => {
  const source = await readFile(component, "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /<BackButton/);
  assert.match(source, /image-generation-create-layout/);
  assert.match(css, /\.image-generation-create-layout[^\n]*grid-template-columns/);
  assert.match(css, /@media \(max-width: 960px\)[\s\S]*\.image-generation-create-layout/);
  assert.match(css, /@media \(max-width: 960px\)[\s\S]*\.image-generation-create-layout \.image-generation-create-guide\s*\{[^}]*display:\s*block/);
  assert.match(css, /@media \(max-width: 700px\)[\s\S]*\.format-guide-grid\s*\{[^}]*grid-template-columns:\s*1fr/);
  assert.match(css, /@media \(max-width: 700px\)[\s\S]*\.image-generation-workspace \.modal-backdrop\s*\{[^}]*left:\s*72px/);
  assert.match(css, /\.image-generation-workspace \.modal-backdrop\s*\{[^}]*z-index:\s*(?:[3-9]\d|[1-9]\d{2,})/);
});

test("model management view renders grouped catalog and QLoRA availability", async () => {
  const source = await readFile(models, "utf8");
  assert.match(source, /image-generation-model-group/);
  assert.match(source, /基座模型/);
  assert.match(source, /训练 Adapter/);
  assert.match(source, /QLoRA/);
  assert.match(source, /不可用/);
  assert.match(source, /image-generation\/models\?provider=youyun/);
  assert.match(source, /全部模型/);
  assert.match(source, /搜索名称/);
  assert.match(source, /打开模型详情/);
  assert.match(source, /模型详情/);
  assert.match(source, /没有匹配的模型/);
  assert.match(source, /登记基模/);
  assert.match(source, /model-assets\/base/);
  assert.match(source, /ModelScope Repo ID/);
  assert.match(source, /服务器本地目录/);
  assert.match(source, /登记并检查/);
  assert.match(source, /浏览基模目录/);
  assert.match(source, /推荐基模/);
  assert.match(source, /登记此基模/);
  assert.match(source, /stabilityai\/stable-diffusion-xl-base-1.0/);
  assert.match(source, /stabilityai\/sdxl-turbo/);
  assert.match(source, /segmind\/SSD-1B/);
  assert.match(source, /Lykon\/dreamshaper-xl-1-0/);
  assert.match(source, /文生图可用/);
  assert.match(source, /需缓存 \/ 探针/);
  assert.match(source, /查看资产/);
  assert.match(source, /自定义模型/);
  assert.match(source, /推荐地址/);
  assert.match(source, /推荐地址.*Tab/);
  assert.match(source, /推荐地址.*onClick/);
  assert.match(source, /suggestedPath/);
  assert.match(source, /显示名称.*event\.key === "Tab"/);
  assert.match(source, /ModelScope Repo ID.*event\.key === "Tab"/);
  assert.match(source, /Revision.*event\.key === "Tab"/);
  assert.match(source, /fillSuggestedField/);
});

test("service view exposes stage-one configuration controls", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /trainingMode/);
  assert.match(source, /LoRA/);
  assert.match(source, /QLoRA/);
  assert.match(source, /width/);
  assert.match(source, /height/);
  assert.match(source, /steps/);
  assert.match(source, /CFG/);
  assert.match(source, /配置已保存，可在上方创建生成任务/);
  assert.match(source, /启动前检查/);
  assert.match(source, /preflight/);
  assert.match(source, /expiresAtEpoch/);
  assert.match(source, /服务描述/);
  assert.match(source, /执行环境/);
  assert.match(source, /默认负面提示词/);
  assert.match(source, /运行参数/);
  assert.match(source, /CPU Offload/);
  assert.match(source, /Attention Slicing/);
  assert.match(source, /VAE Tiling/);
  assert.match(source, /安全检查器/);
  assert.match(source, /precision/);
  assert.match(source, /loadTimeout/);
  assert.match(source, /Array\.isArray\(detail\)/);
  assert.match(source, /校验字段/);
});

test("image-generation inference uses a service-first workspace instead of stacking two full pages", async () => {
  const pageSource = await readFile(page, "utf8");
  const workspaceSource = await readFile(inferenceWorkspace, "utf8");
  assert.match(pageSource, /ImageGenerationInferenceWorkspace/);
  assert.doesNotMatch(pageSource, /<><ImageGenerationTasks \/><ImageGenerationService \/><\/>/);
  assert.match(workspaceSource, /ImageGenerationService/);
  assert.doesNotMatch(workspaceSource, /ImageGenerationTasks/);
});

test("generation is scoped to the selected image-generation service", async () => {
  const workspaceSource = await readFile(inferenceWorkspace, "utf8");
  const serviceSource = await readFile(service, "utf8");
  const taskSource = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(workspaceSource, /生成任务<\/button>/);
  assert.match(serviceSource, /服务概览/);
  assert.match(serviceSource, /图片生成/);
  assert.match(serviceSource, /验证评测/);
  assert.match(serviceSource, /运行日志/);
  assert.match(taskSource, /serviceId: string/);
  assert.match(taskSource, /scopedServiceId\) params\.set\("serviceId", scopedServiceId\)/);
  assert.match(taskSource, /!embedded && <label className="field"><span className="field-label">服务配置/);
});

test("service evaluation validates a published dataset before task creation", async () => {
  const source = await readFile(evaluation, "utf8");
  const serviceSource = await readFile(service, "utf8");
  assert.match(serviceSource, /ImageGenerationEvaluation/);
  assert.match(source, /image-generation\/evaluation-datasets/);
  assert.match(source, /image-generation\/evaluations\/validate/);
  assert.match(source, /services\/\$\{serviceId\}\/evaluations/);
  assert.match(source, /校验评测集/);
  assert.match(source, /创建待确认评测/);
  assert.match(source, /CLIP/);
  assert.match(source, /辅助自动指标/);
});

test("image-generation service mirrors the training list-create-detail flow", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /type ServiceView = "list" \| "create" \| "detail"/);
  assert.match(source, /<PageHeader[^>]*title="文生图推理服务"/);
  assert.match(source, /创建推理服务/);
  assert.match(source, /搜索服务名称或模型/);
  assert.match(source, /<th>服务名称<\/th><th>模型与 Adapter<\/th><th>默认生成参数<\/th><th>优云资源<\/th><th>启动前检查<\/th><th>更新时间<\/th><th>操作<\/th>/);
  assert.match(source, /返回推理服务/);
  assert.match(source, /image-generation-service-create-layout/);
  assert.match(source, /服务基本信息/);
  assert.match(source, /模型与 Adapter/);
  assert.match(source, /优云运行环境/);
  assert.match(source, /gpu-specs\?instanceMode=auto&purpose=inference/);
  assert.match(source, /默认生成参数/);
  assert.match(source, /服务创建摘要/);
  assert.match(source, /生成历史/);
});

test("image-generation service lists every healthy zone flat with mode tags", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /imageSelectionWarning/);
  assert.match(source, /YouyunZonePicker zones=\{healthyYouyunZones\}/);
  assert.doesNotMatch(source, /allowCrossMode/);
});

test("image-generation service logs tab renders persisted task logs and diagnostics", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /logs\?: string\[\]/);
  assert.match(source, /item\.logs/);
  assert.match(source, /failureMessage/);
  assert.match(source, /suggestedAction/);
  assert.match(source, /cleanupState/);
  assert.match(source, /运行日志/);
});

test("image-generation service has responsive list and creation layouts", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.image-generation-service-table table[^\n]*min-width/);
  assert.match(css, /\.image-generation-service-create-layout[^\n]*grid-template-columns/);
  assert.match(css, /@media \(max-width: 960px\)[\s\S]*\.image-generation-service-create-layout/);
  assert.match(css, /@media \(max-width: 700px\)[\s\S]*\.image-generation-service-list-page/);
});

test("training view mirrors the visual task flow and builds Youyun resources from live choices", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /新建文生图微调任务/);
  assert.match(source, /执行环境/);
  assert.match(source, /远程优云 GPU/);
  assert.match(source, /微调方式/);
  assert.match(source, /LoRA/);
  assert.match(source, /QLoRA/);
  assert.match(source, /image-generation\/youyun\/gpu-specs/);
  assert.match(source, /youyun\/price/);
  assert.match(source, /machineTypes/);
  assert.match(source, /capacity/);
  assert.match(source, /recommendedImageId/);
  assert.match(source, /const \[nextDatasets, nextModels, nextTasks\] = await Promise\.all/);
  assert.doesNotMatch(source, /nextTasks, nextSpecs/);
  assert.match(source, /region: specs\.region/);
  assert.match(source, /zone: specs\.zone/);
  assert.match(source, /gpuType:/);
  assert.match(source, /gpuCount: 1/);
  assert.match(source, /cpuCores/);
  assert.match(source, /memoryGb/);
  assert.match(source, /imageId/);
  assert.doesNotMatch(source, /resourceText/);
  assert.doesNotMatch(source, /优云资源快照（JSON）/);
});

test("image-generation training filters incompatible images and runs automatic preflight", async () => {
  const source = await readFile(training, "utf8");

  assert.match(source, /compatibleImages/);
  assert.match(source, /image\.compatible !== false/);
  assert.match(source, /自动校验/);
  assert.match(source, /runAutomaticPreflight/);
  assert.match(source, /推荐理由/);
});

test("image-generation training can explicitly probe a matching community image", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /验证此镜像/);
  assert.match(source, /community-images\/probe/);
  assert.match(source, /purpose:\s*"image_generation_training"/);
  assert.match(source, /探针会创建临时优云实例/);
  assert.match(source, /cleanupState/);
});

test("image-generation training exposes explicit Youyun Pod and UHost fallback selection", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /selectedYouyunInstanceMode/);
  assert.match(source, /优云 UHost 兜底/);
  assert.match(source, /instanceMode: selectedYouyunInstanceMode/);
  assert.match(source, /YouyunZonePicker zones=\{healthyYouyunZones\}/);
  assert.match(source, /setSelectedYouyunInstanceMode\(youyunPreferredMode\(zone, undefined, selectedYouyunInstanceMode\)\)/);
});

test("training view defaults to a 24GB-friendly SDXL LoRA configuration", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /useState\(512\)/);
  assert.match(source, /useState\(4\)/);
});

test("training view opens on a task table and moves creation into a secondary view", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /const \[view, setView\] = useState<TrainingView>\("list"\)/);
  assert.match(source, /<PageHeader[^>]*title="微调任务"/);
  assert.match(source, /新建微调任务/);
  assert.match(source, /搜索任务名称或任务 ID/);
  assert.match(source, /全部状态/);
  assert.match(source, /<th>任务名称<\/th><th>基础模型<\/th><th>数据集<\/th><th>训练方式<\/th><th>状态 \/ 进度<\/th><th>创建时间<\/th><th>操作<\/th>/);
  assert.match(source, /view === "list"/);
  assert.match(source, /返回微调任务/);
  assert.match(source, /image-generation-training-table/);
});

test("image-generation training tasks open a visual-style detail workspace", async () => {
  const source = await readFile(training, "utf8");
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(source, /type TrainingView = "list" \| "create" \| "detail"/);
  assert.match(source, /async function openDetail/);
  assert.match(source, /refreshDetail\(task\.id\)/);
  assert.match(source, /image-generation\/training\/tasks\/\$\{taskId\}/);
  assert.match(source, /image-generation\/training\/tasks\/\$\{taskId\}\/logs/);
  assert.match(source, /image-generation\/training\/tasks\/\$\{taskId\}\/metrics/);
  assert.match(source, /className="dataset-link"/);
  assert.match(source, /onClick=\{\(\) => void openDetail\(task\)\}/);
  assert.match(source, />详细<\/button>/);
  assert.match(source, /view === "detail"/);
  assert.match(source, /返回微调任务/);
  assert.match(source, /任务阶段/);
  assert.match(source, /训练进度/);
  assert.match(source, /配置快照/);
  assert.match(source, /运行日志/);
  assert.match(source, /训练输出/);
  assert.match(source, /attempts/);
  assert.match(source, /阶段耗时/);
  assert.match(css, /\.image-generation-training-detail-page \.vision-stage-timeline \.status[^\n]*white-space:\s*nowrap/);
});

test("image-generation training exposes manual Youyun cleanup recovery", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /youyun\/release/);
  assert.match(source, /释放优云实例/);
  assert.match(source, /cleanupState/);
});

test("image-generation training and services expose direct delete confirmation", async () => {
  const [trainingSource, serviceSource] = await Promise.all([
    readFile(training, "utf8"),
    readFile(service, "utf8"),
  ]);
  assert.match(trainingSource, /Trash2/);
  assert.match(trainingSource, /method:\s*["']DELETE["']/);
  assert.match(trainingSource, /确认删除微调任务/);
  assert.match(trainingSource, /永久删除任务记录、日志、训练目录和 Adapter 产物/);
  assert.match(serviceSource, /Trash2/);
  assert.match(serviceSource, /method:\s*["']DELETE["']/);
  assert.match(serviceSource, /确认删除推理服务/);
  assert.match(serviceSource, /永久删除服务配置、日志和指标/);
  assert.match(trainingSource, /role="dialog"/);
  assert.match(serviceSource, /role="dialog"/);
});

test("training view follows the reference light workspace visual language", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.image-generation-training-page[^\n]*background/);
  assert.match(css, /\.image-generation-training-page .*\.primary-btn[^\n]*#1b58f1/i);
  assert.match(css, /\.image-generation-training-page .*\.secondary-btn[^\n]*border-radius:\s*8px/i);
  assert.match(css, /\.image-generation-training-page .*\.panel[^\n]*border-radius:\s*10px/i);
  assert.match(css, /\.image-generation-training-page .*\.input[^\n]*border-radius:\s*8px/i);
});

test("image-generation training and inference select Pod regions with linked inventory", async () => {
  const [trainingSource, serviceSource] = await Promise.all([readFile(training, "utf8"), readFile(service, "utf8")]);

  for (const source of [trainingSource, serviceSource]) {
    assert.match(source, /youyun-location|youyun-zone-picker/);
    assert.match(source, /selectedYouyunRegion/);
    assert.match(source, /selectedYouyunZone/);
    assert.match(source, /YouyunZonePicker/);
    assert.match(source, /selectYouyunZone/);
    assert.match(source, /region:\s*selectedYouyunRegion/);
    assert.match(source, /zone:\s*selectedYouyunZone/);
  }
});

test("training actions surface structured backend blockers instead of a generic request failure", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /formatRequestError/);
  assert.match(source, /structured\.reasons/);
  assert.match(source, /checks/);
  assert.match(source, /messages\.join/);
  assert.match(source, /正在确认并复检优云资源/);
  assert.match(source, /任务已确认并进入训练队列/);
});

test("image-generation submission shows progress while the request is running", async () => {
  const source = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  const confirmStart = source.indexOf('async function act(action: "confirm" | "stop" | "retry")');
  const busyStart = source.indexOf("setBusy(true);", confirmStart);
  const submitRequest = source.indexOf("/image-generation/tasks/${selected.id}/confirm", confirmStart);
  assert.ok(confirmStart >= 0, "confirmation action should exist");
  assert.ok(busyStart >= 0 && submitRequest >= 0 && busyStart < submitRequest, "busy state must be set before submit request");
  assert.match(source, /提交生成/);
});

test("image-generation service exposes a POST preflight contract", async () => {
  const source = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.match(source, /preflight/);
});

test("image-generation draft tasks explain why progress remains at zero", async () => {
  const source = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.match(source, /任务已保存，尚未提交/);
  assert.match(source, /提交生成/);
  assert.match(source, /不会创建新的优云实例/);
});

test("image-generation submission does not use a paid confirmation dialog", async () => {
  const source = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /window\.confirm\("优云任务会产生费用/);
  assert.doesNotMatch(source, /确认启动优云任务/);
  assert.doesNotMatch(source, /confirmStart/);
});

test("image-generation preflight exposes blocked capacity details", async () => {
  const source = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.match(source, /优云启动前检查未通过/);
  assert.match(source, /capacity/);
  assert.match(source, /所选 GPU 当前无库存/);
  assert.match(source, /优云库存和价格会动态变化，请刷新资源后重新检查/);
});

test("image-generation preflight failures preserve structured details and expose retry", async () => {
  const source = await readFile(new URL("../components/image-generation-tasks.tsx", import.meta.url), "utf8");
  assert.match(source, /error\.detail = detail/);
  assert.match(source, /detail\?\.preflight/);
  assert.match(source, /优云启动前检查未通过/);
  assert.match(source, /重新检查/);
});

test("image-generation service shows startup progress and polls while starting", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /STARTUP_STAGE_LABELS/);
  assert.match(source, /startupStatusLabel/);
  assert.match(source, /创建优云实例/);
  assert.match(source, /准备 Diffusers 运行环境/);
  assert.match(source, /解析基座模型/);
  assert.match(source, /上传 Adapter/);
  assert.match(source, /startupStage\?: string \| null/);
  assert.match(source, /startupStartedAt\?: string \| null/);
  assert.match(source, /已进行 \$\{minutes\} 分钟/);
  assert.match(source, /hasStartingService/);
  assert.match(source, /setInterval/);
  assert.match(source, /refreshServices/);
  assert.match(source, /\/image-generation\/services\/\$\{selectedId\}\/logs\?limit=200/);
  assert.match(source, /页面每 5 秒自动刷新状态，运行日志请查看「运行日志」页签/);
  assert.match(source, /服务日志/);
  assert.match(source, /image-generation-service-log-lines/);
  assert.match(source, /实例方案：/);
  assert.match(source, /推荐镜像：/);
  assert.match(source, /改选预置 Diffusers 镜像可显著加快启动/);
  assert.match(source, /优云镜像目录当前没有预装 Diffusers 的镜像/);
  assert.match(source, /specs\?\.requiresDiffusersInstall \? \(/);
  assert.match(source, /service\.state === "starting" && <span className="field-hint">\{startupStatusLabel\(service\)\?\.text\}<\/span>/);
});

test("image-generation service warns when the chosen image needs remote diffusers install", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /当前镜像未预装 Diffusers/);
  assert.match(source, /预计需要 20 分钟以上/);
  assert.match(source, /建议改选预置 Diffusers 镜像/);
});

test("image-generation service list renders before youyun specs finish loading", async () => {
  const source = await readFile(service, "utf8");
  assert.match(source, /const loadServices = useCallback/);
  assert.match(source, /const loadCatalog = useCallback/);
  assert.match(source, /void loadServices\(\);\n    void loadCatalog\(\);/);
  assert.match(source, /await Promise\.all\(\[loadServices\(\), loadCatalog\(\)\]\)/);
  const loadServicesBody = source.slice(
    source.indexOf("const loadServices"),
    source.indexOf("const loadCatalog"),
  );
  assert.doesNotMatch(loadServicesBody, /gpu-specs/);
});

test("image-generation service list uses compact row spacing to fit one page", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.image-generation-service-page \.image-generation-service-table td \{ height: 54px; min-height: 0; padding-top: 6px; padding-bottom: 6px; \}/);
  assert.match(css, /\.image-generation-service-page \.image-generation-service-table th \{ height: 40px; \}/);
  assert.match(css, /\.image-generation-service-page \.image-generation-service-table \.row-sub \{ max-width: 190px; overflow: hidden; margin-top: 2px;/);
});

test("image-generation service list keeps action buttons on one line", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.image-generation-service-page \.image-generation-service-table th:last-child,\n\.image-generation-service-page \.image-generation-service-table td:last-child \{ width: 220px; \}/);
  assert.match(css, /\.image-generation-service-page \.image-generation-service-table \.actions \{ column-gap: 10px; row-gap: 2px; \}/);
  assert.match(css, /\.image-generation-service-table \.actions \.field-hint \{ max-width: 100%; overflow: hidden;/);
  assert.match(css, /\.image-generation-service-page \.image-generation-service-table table \{ min-width: 0; width: 100%; \}/);
});

test("image-generation model table mutes badge colors and tightens spacing", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.model-page-image-generation \.image-generation-model-table \{ min-width: 0; width: 100%; \}/);
  assert.match(css, /\.model-page-image-generation \.image-generation-model-table td \{ height: 60px; min-height: 0; \}/);
  assert.match(css, /\.model-page-image-generation \.image-generation-model-table \.source-tag \{ background: #f2f4f7; color: #475467; \}/);
  assert.match(css, /\.model-page-image-generation \.image-generation-mode-list \.method-tag\.supported \{ background: #f2f4f7; color: #344054; \}/);
  assert.match(css, /\.model-page-image-generation \.model-avatar\.trained \{ background: #f2f4f7; color: #475467; \}/);
});

test("image-generation training list fits container with compact rows", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.image-generation-training-page \.image-generation-training-table table \{ min-width: 0; width: 100%; \}/);
  assert.match(css, /\.image-generation-training-page \.image-generation-training-table td \{ height: 60px; min-height: 0;/);
  assert.match(css, /\.image-generation-training-page \.image-generation-training-table th:last-child,\n\.image-generation-training-page \.image-generation-training-table td:last-child \{ width: 230px; \}/);
});

test("image-generation training list uses fixed layout so it never overflows", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.image-generation-training-page \.image-generation-training-table table \{ table-layout: fixed; \}/);
  assert.match(css, /\.image-generation-training-page \.image-generation-training-table th:nth-child\(7\) \{ width: 13%; \}/);
  assert.match(css, /\.image-generation-training-page \.image-generation-training-table \.row-sub \{ max-width: 100%; \}/);
});

test("shared zone picker renders every youyun zone flat with mode tags", async () => {
  const [component, libSource, trainingSource] = await Promise.all([
    readFile(new URL("../components/youyun-zone-picker.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/youyun-location.ts", import.meta.url), "utf8"),
    readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(component, /youyunZoneLabel/);
  assert.match(component, /zones\.map\(\(zone\)/);
  assert.match(component, /youyun-zone-mode">\{youyunZoneModeLabel\(zone\)\}/);
  assert.doesNotMatch(component, /is_pod === \(mode === "pod"\)/);
  assert.doesNotMatch(component, /allowCrossMode/);
  assert.match(component, /个可用区 · 来自优云实时目录/);
  assert.match(libSource, /export function youyunZoneLabel/);
  assert.match(trainingSource, /setSelectedYouyunInstanceMode\(youyunPreferredMode\(zone, undefined, selectedYouyunInstanceMode\)\)/);
});

test("youyun zone picker uses an aligned grid with mode badges", async () => {
  const [component, css] = await Promise.all([
    readFile(new URL("../components/youyun-zone-picker.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(component, /youyun-zone-mode/);
  assert.match(component, /youyun-zone-name/);
  assert.doesNotMatch(component, /· \{zone\.is_pod/);
  assert.match(css, /\.youyun-location-picker \.youyun-region-list \{ display: grid; grid-template-columns: repeat\(auto-fill, minmax\(148px, 1fr\)\); gap: 8px; \}/);
  assert.match(css, /\.youyun-location-picker \.youyun-zone-mode \{ flex: 0 0 auto; padding: 2px 6px; border-radius: 999px;/);
});

test("vision youyun config block uses label-top fields, constraint callout and dividers", async () => {
  const [source, css] = await Promise.all([
    readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  assert.match(source, /youyun-specs-panel-v2/);
  assert.match(source, /youyun-constraint-callout/);
  assert.match(source, /当前仅支持单卡任务/);
  assert.match(source, /className="youyun-field-label">GPU 型号 · 单卡/);
  assert.match(source, /className="youyun-field-label">单卡规格 · CPU \/ 内存/);
  assert.match(source, /className="youyun-field-row"/);
  assert.doesNotMatch(source, /fixed-option-row youyun-instance-fixed/);
  assert.doesNotMatch(source, /youyun-selection-summary/);
  assert.match(css, /\.youyun-constraint-callout \{[^}]*border-left: 3px solid #d92d20/);
  assert.match(css, /\.youyun-field \{ display: flex; flex-direction: column;/);
  assert.match(css, /\.youyun-field-row \{ display: grid; grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); gap: 18px; \}/);
});

test("image-generation training youyun block uses the same v2 layout", async () => {
  const source = await readFile(training, "utf8");
  assert.match(source, /youyun-specs-panel image-generation-youyun-specs-panel youyun-specs-panel-v2/);
  assert.match(source, /youyun-constraint-callout/);
  assert.match(source, /当前仅支持单卡任务/);
  assert.match(source, /className="youyun-field-label">GPU 型号 · 单卡/);
  assert.match(source, /className="youyun-field-label">单卡规格 · CPU \/ 内存/);
  assert.doesNotMatch(source, /fixed-option-row youyun-instance-fixed/);
  assert.doesNotMatch(source, /youyun-selection-summary"><strong>GPU 型号/);
  assert.doesNotMatch(source, /youyun-selection-summary"><strong>单卡 CPU/);
});

test("vision training create view scrolls naturally like image-generation", async () => {
  const source = await readFile(new URL("../components/vision-training-module.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /vision-create-fixed/);
  assert.doesNotMatch(source, /vision-create-scroll/);
  assert.match(source, /content tuning-content vision-training-page/);
});
