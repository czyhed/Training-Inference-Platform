import { apiUrl } from "./api-config";

export type SpeechMode = "design" | "clone" | "continue" | "combined";
export type SpeechRequest = {
  text: string; mode: SpeechMode; steps: number; cfg: number; seed: number;
  minTokens: number; maxTokens: number; normalize: boolean; retryBadcase: boolean;
  retryMaxTimes: number; retryRatio: number; streaming: boolean;
  referenceAudioId: string | null; promptAudioId: string | null; promptText: string | null; requestId?: string;
};
export const speechDefaults: SpeechRequest = { text: "你好，欢迎来到 XShipAI 工作台。让文字拥有声音，让创意被听见。", mode: "design", steps: 10, cfg: 2, seed: 42, minTokens: 2, maxTokens: 1024, normalize: false, retryBadcase: false, retryMaxTimes: 2, retryRatio: 6, streaming: false, referenceAudioId: null, promptAudioId: null, promptText: null };
export type SpeechService = { id: string; name: string; repoId: string; state: string; updatedAt: string; failureCode?: string; activeJobId: string | null; queuedJobs: number; threads: number; modelVerified: boolean; runtimeVersion: string; sampleRate: number };
export type SpeechJob = SpeechRequest & { id: string; state: string; createdAt: string; updatedAt: string; message?: string; failureCode?: string; durationSeconds?: number; generationSeconds?: number; sampleRate?: number; audioBytes?: number; sha256?: string; chunkCount: number; generatedSeconds?: number };
export type SpeechAsset = { id: string; name: string; durationSeconds: number; sampleRate: number; audioBytes: number; createdAt: string; sha256: string };
export const modeLabels: Record<SpeechMode,string> = { design: "声音设计", clone: "音色克隆", continue: "音频续写", combined: "克隆 + 续写" };
export const stateLabels: Record<string,string> = { stopped: "已停止", loading: "加载与预热中", ready: "可用", stopping: "停止中", failed: "失败", running: "生成中", queued: "排队中", completed: "已完成", cancelled: "已取消" };
function record(value: unknown): Record<string,unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("语音服务返回了无效的数据");
  return value as Record<string,unknown>;
}
function fields(value: unknown, names: string[]): Record<string,unknown> {
  const data = record(value);
  if (names.some(name => typeof data[name] !== "string")) throw new Error("语音服务响应缺少必要字段");
  return data;
}
function service(value: unknown): SpeechService {
  const data = fields(value,["id","name","state","updatedAt","repoId"]);
  if (typeof data.threads !== "number" || typeof data.modelVerified !== "boolean") throw new Error("模型状态响应不完整");
  return data as unknown as SpeechService;
}
function job(value: unknown): SpeechJob {
  const data = fields(value,["id","state","text","createdAt"]);
  if (!/^tts-[a-f0-9]{32}$/.test(data.id as string)) throw new Error("任务标识无效");
  return { ...speechDefaults, chunkCount: 0, ...data } as SpeechJob;
}
function asset(value: unknown): SpeechAsset {
  const data = fields(value,["id","name","createdAt"]);
  if (!/^aud-[a-f0-9]{32}$/.test(data.id as string) || typeof data.durationSeconds !== "number") throw new Error("音频素材响应不完整");
  return data as unknown as SpeechAsset;
}
function items<T>(value: unknown, parse: (value: unknown) => T): T[] {
  const data = record(value);
  if (!Array.isArray(data.items)) throw new Error("列表响应不完整");
  return data.items.map(parse);
}
async function call(path: string, options: RequestInit = {}): Promise<unknown> {
  const response = await fetch(apiUrl("speech/" + path), { ...options, credentials: "include", cache: "no-store" });
  const body: unknown = await response.json().catch(() => null);
  if (!body) throw new Error("无法连接语音服务，请确认工作台登录仍然有效");
  const envelope = record(body);
  if (!response.ok || envelope.error) {
    const error = envelope.error ? record(envelope.error) : null;
    throw new Error(typeof error?.message === "string" ? error.message : `语音请求失败（${response.status}）`);
  }
  return envelope.data;
}
const json = (body: unknown, signal?: AbortSignal): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
export const speechApi = {
  service: async (signal?: AbortSignal) => service(await call("service", { signal })),
  jobs: async (signal?: AbortSignal) => items(await call("jobs?pageSize=50", { signal }), job),
  assets: async (signal?: AbortSignal) => items(await call("assets?pageSize=100", { signal }), asset),
  lifecycle: async (action: "start" | "stop", signal?: AbortSignal) => service(await call("service/" + action, json({}, signal))),
  generate: async (body: SpeechRequest, signal?: AbortSignal) => job(await call("jobs", json(body, signal))),
  batch: async (requests: SpeechRequest[], signal?: AbortSignal) => items(await call("batches", json({ items: requests }, signal)), job),
  action: async (id: string, action: "cancel" | "retry", signal?: AbortSignal) => job(await call(`jobs/${id}/${action}`, json({}, signal))),
  deleteJob: (id: string, signal?: AbortSignal) => call(`jobs/${id}`, { method: "DELETE", signal }),
  deleteAsset: (id: string, signal?: AbortSignal) => call(`assets/${id}`, { method: "DELETE", signal }),
  upload: async (file: Blob, name: string, signal?: AbortSignal) => asset(await call(`assets?name=${encodeURIComponent(name.slice(0,120))}`, { method: "POST", body: file, signal, headers: { "Content-Type": "application/octet-stream" } })),
};
export const speechAudio = (id: string, kind: "jobs" | "assets" = "jobs") => apiUrl(`speech/${kind}/${id}/audio`);
export const speechChunk = (id: string, index: number) => apiUrl(`speech/jobs/${id}/chunks/${index}`);
export const requestId = () => globalThis.crypto?.randomUUID?.() || `web-${Date.now()}-${Math.random().toString(36).slice(2)}`;
