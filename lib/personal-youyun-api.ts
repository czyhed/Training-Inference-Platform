import { apiUrl } from "./api-config";

export type YouyunSettings = {
  configured: boolean;
  publicKeyMasked: string;
  region: string;
  zone: string;
};
export type YouyunSettingsInput = { publicKey: string; privateKey: string; region: string; zone: string };

async function request<T>(suffix: string, signal: AbortSignal, method = "GET", value?: YouyunSettingsInput): Promise<T> {
  const response = await fetch(apiUrl(`/settings/youyun${suffix}`), {
    method, signal, credentials: "include", cache: "no-store",
    headers: value ? { "Content-Type": "application/json" } : undefined,
    body: value ? JSON.stringify(value) : undefined,
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "优云配置请求失败，请重试。");
  return result as T;
}

export const loadYouyunSettings = (signal: AbortSignal) => request<YouyunSettings>("", signal);
export const saveYouyunSettings = (value: YouyunSettingsInput, signal: AbortSignal) => request<YouyunSettings>("", signal, "PUT", value);
export const testYouyunConnection = (signal: AbortSignal) => request<{ connected: boolean; message: string }>("/test", signal, "POST");
