import { apiUrl } from "./api-config";

export function modelAssetErrorMessage(body: unknown, status: number): string {
  if (body && typeof body === "object") {
    const value = body as Record<string, unknown>;
    const detail = value.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    const error = value.error;
    if (error && typeof error === "object" && typeof (error as Record<string, unknown>).message === "string") return String((error as Record<string, unknown>).message);
  }
  return `模型接口请求失败（${status}）`;
}

export async function modelAssetRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...options, credentials: "include", cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(modelAssetErrorMessage(body, response.status));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
