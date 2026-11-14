"use client";
import { useState } from "react";
import { apiUrl } from "../lib/api-config";
import { SpeechRequest } from "../lib/speech-api";
import s from "./speech-module.module.css";

export function saveFile(name: string, content: string, type = "application/json") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url),1000);
}
export async function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const input = document.createElement("textarea"); input.value = text; input.style.position = "fixed"; input.style.opacity = "0";
  document.body.appendChild(input); input.select(); const copied = document.execCommand("copy"); input.remove();
  if (!copied) throw new Error("复制失败，请选择代码后手动复制");
}
const endpoints = [
  ["GET","/service","状态、能力、活动任务、排队数量"], ["POST","/service/start","启动模型，loading → ready"], ["POST","/service/stop","停止模型并取消全部待处理任务"],
  ["POST","/assets?name=sample.wav","上传原始音频字节（不是 multipart）；201 返回素材ID"], ["GET","/assets?page=1&pageSize=20","分页列出素材"], ["GET","/assets/{assetId}/audio","读取标准化的参考音频"], ["DELETE","/assets/{assetId}","删除未被历史任务引用的素材"],
  ["POST","/jobs","创建单任务，202；CPU忙碌时409"], ["POST","/batches","以 {items:[…]} 提交1–5个任务，202"], ["GET","/jobs?page=1&pageSize=20&state=completed&query=你好","分页、状态筛选、文本搜索"],
  ["GET","/jobs/{jobId}","任务状态、参数、时长、耗时、SHA256"], ["POST","/jobs/{jobId}/cancel","取消任务；运行中任务会终止进程并重新加载模型"], ["POST","/jobs/{jobId}/retry","用原参数创建新任务，返回新的ID"], ["DELETE","/jobs/{jobId}","删除终态任务及音频；活动任务须先取消"],
  ["GET","/jobs/{jobId}/audio","完成后下载48kHz单声道PCM16 WAV"], ["GET","/jobs/{jobId}/chunks/{index}","读取流式WAV片段；0 ≤ index < chunkCount"],
];
const parameters = [
  ["text","string · 必填","1–300字","目标文本，括号内可描述语气、情绪或声音特点"], ["mode","string","design","design / clone / continue / combined"],
  ["referenceAudioId","string | null","null","clone、combined 必填；其他模式必须为空"], ["promptAudioId / promptText","string | null","null","continue、combined 配对填写；文本须与提示音频一致"],
  ["steps","integer","10 · 4–20","扩散推理步数，更高通常更慢"], ["cfg","number","2 · 1–4","文本引导强度，影响遵循程度和自然度"], ["seed","integer","42 · 0–2147483647","用于 Torch / NumPy / Python；不同环境不保证逐字节一致"],
  ["minTokens / maxTokens","integer","2 / 1024","1–1024 / 16–2048，最小须小于最大；是生成长度单位，非秒"], ["normalize","boolean","false","文本规范化，例如数字、日期及单位"],
  ["retryBadcase","boolean","false","非流式生成时按音频/文本比例自动重试"], ["retryMaxTimes / retryRatio","integer / number","2 / 6","0–3次 / 1–20阈值；重试仍受20分钟总期限限制"], ["streaming","boolean","false","逐片段保存和返回；流式时不执行badcase重试"],
  ["requestId","string","自动生成","1–80位字母、数字、_或-；重试网络请求时复用该值"],
];

export function SpeechApiDocs({ request }: { request: SpeechRequest }) {
  const [language,setLanguage] = useState("Python");
  const [notice,setNotice] = useState("");
  const origin = typeof window === "undefined" ? "http://23.144.68.140:18181" : window.location.origin;
  const desktop = new URL(origin); desktop.port = "18180";
  const payload = { ...request, requestId: "example-" + "001" };
  const json = JSON.stringify(payload,null,2);
  const python = `# Python 3.11+，仅使用标准库。设置 XSHIPAI_USERNAME 和 XSHIPAI_PASSWORD。
# 如需上传音频：设置 REFERENCE_AUDIO / PROMPT_AUDIO 文件路径与 PROMPT_TEXT。
import os, json, time, uuid, urllib.request, urllib.parse, http.cookiejar
from pathlib import Path

DESKTOP = os.getenv("XSHIPAI_DESKTOP", ${JSON.stringify(desktop.origin)})
BASE = os.getenv("XSHIPAI_SPEECH_API", ${JSON.stringify(origin + apiUrl("speech"))})
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call(url, body=None, method=None, headers=None):
    if isinstance(body, dict):
        body = json.dumps(body, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with client.open(req, timeout=60) as response:
        raw = response.read()
        return json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

call(DESKTOP + "/api/login", {"username": os.environ["XSHIPAI_USERNAME"], "password": os.environ["XSHIPAI_PASSWORD"]}, headers={"Origin": DESKTOP})
session = call(DESKTOP + "/api/session")
call(DESKTOP + "/api/launch/cppt", {}, headers={"Origin": DESKTOP, "X-CSRF-Token": session["csrf"]})
call(BASE + "/service/start", {})
deadline = time.monotonic() + 1200
while True:
    state = call(BASE + "/service")["data"]["state"]
    if state == "ready": break
    if state == "failed" or time.monotonic() > deadline: raise RuntimeError("模型启动失败或超时")
    time.sleep(3)

payload = json.loads(${JSON.stringify(json)})
payload["requestId"] = uuid.uuid4().hex
for env, field in [("REFERENCE_AUDIO", "referenceAudioId"), ("PROMPT_AUDIO", "promptAudioId")]:
    if os.getenv(env):
        path = Path(os.environ[env])
        uploaded = call(BASE + "/assets?" + urllib.parse.urlencode({"name": path.name}), path.read_bytes(), method="POST", headers={"Content-Type": "application/octet-stream"})
        payload[field] = uploaded["data"]["id"]
if os.getenv("PROMPT_TEXT"): payload["promptText"] = os.environ["PROMPT_TEXT"]
job = call(BASE + "/batches", {"items": [payload]})["data"]["items"][0]
print("task", job["id"])
deadline = time.monotonic() + 2400
while job["state"] in ("queued", "running"):
    if time.monotonic() > deadline: raise TimeoutError("任务仍在运行，请按ID查询或取消")
    time.sleep(3)
    job = call(BASE + "/jobs/" + job["id"])["data"]
if job["state"] != "completed": raise RuntimeError(job)
Path("voxcpm2.wav").write_bytes(call(BASE + "/jobs/" + job["id"] + "/audio"))
print("voxcpm2.wav", job["durationSeconds"], "seconds")`;
  const curl = `# Bash + curl + Python 3。先在环境变量中设置用户名和密码。
DESKTOP=${JSON.stringify(desktop.origin)}
BASE=${JSON.stringify(origin + apiUrl("speech"))}
AUTH=$(python -c 'import json,os; print(json.dumps({"username":os.environ["XSHIPAI_USERNAME"],"password":os.environ["XSHIPAI_PASSWORD"]}))')
curl --fail-with-body -c cookies.txt -H "Origin: $DESKTOP" -H 'Content-Type: application/json' -d "$AUTH" "$DESKTOP/api/login"
CSRF=$(curl --fail -b cookies.txt "$DESKTOP/api/session" | python -c 'import json,sys; print(json.load(sys.stdin)["csrf"])')
curl --fail-with-body -b cookies.txt -H "Origin: $DESKTOP" -H "X-CSRF-Token: $CSRF" -X POST "$DESKTOP/api/launch/cppt"
curl --fail-with-body -b cookies.txt -X POST "$BASE/service/start"
# 查询 service，等 data.state 为 ready 后提交。
curl --fail-with-body -b cookies.txt "$BASE/service"
cat > request.json <<'JSON'
${json}
JSON
curl --fail-with-body -b cookies.txt -H 'Content-Type: application/json' --data-binary @request.json "$BASE/jobs"
# 将下面 JOB_ID 替换为返回的 data.id，完成后下载。
JOB_ID=tts_REPLACE_WITH_RETURNED_ID
curl --fail-with-body -b cookies.txt "$BASE/jobs/$JOB_ID"
curl --fail-with-body -b cookies.txt "$BASE/jobs/$JOB_ID/audio" -o voxcpm2.wav
# 参考音频上传示例：
# curl --fail-with-body -b cookies.txt -X POST --data-binary @reference.wav "$BASE/assets?name=reference.wav"
# 用完后删除本地 cookies.txt。`;
  const js = `// 在已登录训推平台的浏览器控制台执行。页面与 API 同源。
const base = ${JSON.stringify(apiUrl("speech"))};
const body = ${json};
body.requestId = "js-" + Date.now();
const response = await fetch(base + "/jobs", {
  method: "POST", credentials: "include",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
});
const result = await response.json();
if (!response.ok) throw new Error(result.error.message);
const id = result.data.id;
let job;
do {
  await new Promise(resolve => setTimeout(resolve, 3000));
  job = (await (await fetch(base + "/jobs/" + id, {credentials:"include"})).json()).data;
  console.log(job.state, job.chunkCount);
} while (["queued", "running"].includes(job.state));
if (job.state === "completed") window.open(base + "/jobs/" + id + "/audio");`;
  const code = language === "Python" ? python : language === "cURL" ? curl : js;
  return <div className={s.docs}>
    <div className={s.row}><div><h2>接口文档</h2><p>页面与程序调用使用同一套接口、参数和任务记录。</p></div><a href={apiUrl("speech/openapi.json")} target="_blank" rel="noreferrer">下载 OpenAPI 3.0 定义</a></div>
    <div className={s.notice}><b>请求地址：</b><code>{origin + apiUrl("speech")}</code><br />鉴权沿用 XShipAI 工作台会话：登录 → 获取 CSRF → 启动训推平台 → 携带 Cookie 调用。会话有效期 8 小时，过期需重新登录；未登录可能收到 303 跳转。私有 worker 密钥不用于外部调用。</div>
    <h3>可运行示例</h3><p>示例同步采用“在线使用”的当前参数。首次调用建议使用声音设计和短句；克隆/续写请先上传音频或设置示例中的文件环境变量。</p>
    <div className={s.row}><div className={s.tabs}>{["Python","cURL","JavaScript"].map(item => <button key={item} className={language===item?s.active:""} onClick={() => setLanguage(item)}>{item}</button>)}</div><div className={s.actions}><button onClick={() => { void copyText(code).then(() => setNotice("已复制示例")).catch(e => setNotice(e.message)); }}>复制代码</button><button onClick={() => saveFile(language==="Python"?"voxcpm2-client.py":language==="cURL"?"voxcpm2-client.sh":"voxcpm2-client.js",code,"text/plain")}>下载示例</button></div></div>
    {notice && <p role="status">{notice}</p>}<pre className={s.code}>{code}</pre>
    <h3>生成参数</h3><div className={s.tableWrap}><table><thead><tr><th>字段</th><th>类型</th><th>默认值 / 范围</th><th>说明</th></tr></thead><tbody>{parameters.map(row => <tr key={row[0]}>{row.map((cell,i) => <td key={i}>{cell}</td>)}</tr>)}</tbody></table></div>
    <h3>接口索引</h3><div className={s.tableWrap}><table><thead><tr><th>方法</th><th>路径（相对请求地址）</th><th>用途</th></tr></thead><tbody>{endpoints.map(row => <tr key={row[0]+row[1]}>{row.map((cell,i) => <td key={i}><code>{cell}</code></td>)}</tr>)}</tbody></table></div>
    <h3>响应、状态与流式读取</h3><pre className={s.code}>{JSON.stringify({requestId:"req-…",traceId:"req-…",data:{id:"tts-…",state:"running",chunkCount:0},error:null,meta:{timestamp:"2026-11-01T01:00:00Z",apiVersion:"v1"}},null,2)}</pre>
    <p>任务状态为 queued → running → completed / failed / cancelled。流式任务每生成一个片段会增加 chunkCount；按从 0 开始的顺序读取片段 WAV，不能直接拼接 WAV 文件头。页面支持连续试听；全部完成后 /audio 始终返回完整 WAV。</p>
    <p>服务一次执行一个任务，最多等待 5 个；每个任务运行期限 20 分钟（不含排队）。保留最近 50 条任务和最多 100 个音频素材。原始上传限制 10 MB、0.5–30 秒，保存为 24 kHz 单声道；输出为 48 kHz WAV。服务停止会取消活动与排队任务；进程/容器重启后未完成任务标记失败。取消运行中任务会重新加载模型，排队任务在预热成功后继续。</p>
    <h3>错误处理</h3><p>读取 error.code 和 error.message；常见代码包括 INVALID_ARGUMENT、INVALID_AUDIO（400），JOB_NOT_FOUND / AUDIO_NOT_FOUND（404），SERVICE_BUSY / QUEUE_FULL / REQUEST_CONFLICT / ASSET_REFERENCED（409），UPLOAD_TOO_LARGE（413），SPEECH_WORKER_UNAVAILABLE（503）。只有网络失败适合用相同 requestId 重试；修改参数或重新生成应使用新标识。</p>
    <p>CPU 部署未加载独立 ZipEnhancer 降噪模型和 LoRA Adapter，因此不接受 denoise、lora 或任意本地路径参数。训练和加载其他模型属于模型部署操作。</p>
  </div>;
}
