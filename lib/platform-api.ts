import { apiUrl } from "./api-config";
import type { PlatformEnvelope, PlatformError } from "./platform-contracts";

export class PlatformRequestError extends Error {
  readonly code: string;
  readonly details: Array<Record<string, unknown>>;
  readonly retryable: boolean;
  readonly requestId?: string;
  readonly traceId?: string;

  constructor(error: PlatformError, envelope?: PlatformEnvelope<unknown>, fallbackStatus?: number) {
    super(error.message || `请求失败（${fallbackStatus ?? "未知"}）`);
    this.name = "PlatformRequestError";
    this.code = error.code;
    this.details = error.details ?? [];
    this.retryable = Boolean(error.retryable);
    this.requestId = envelope?.requestId;
    this.traceId = envelope?.traceId;
  }
}

function normalizeError(value: unknown, status?: number): PlatformError {
  if (value && typeof value === "object") {
    const candidate = value as Partial<PlatformEnvelope<unknown>> & Partial<PlatformError>;
    if (candidate.error && typeof candidate.error === "object") {
      return {
        code: String(candidate.error.code ?? "REQUEST_FAILED"),
        message: String(candidate.error.message ?? `请求失败（${status ?? "未知"}）`),
        details: Array.isArray(candidate.error.details) ? candidate.error.details as Array<Record<string, unknown>> : [],
        retryable: Boolean(candidate.error.retryable),
      };
    }
    if (candidate.code || candidate.message) {
      return {
        code: String(candidate.code ?? "REQUEST_FAILED"),
        message: String(candidate.message ?? `请求失败（${status ?? "未知"}）`),
        details: Array.isArray(candidate.details) ? candidate.details as Array<Record<string, unknown>> : [],
        retryable: Boolean(candidate.retryable),
      };
    }
  }
  return {
    code: "REQUEST_FAILED",
    message: `请求失败（${status ?? "未知"}）`,
    details: [],
    retryable: false,
  };
}

export async function platformRequest<T>(
  path: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    credentials: "include",
    cache: "no-store",
    signal: signal ?? options.signal,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new PlatformRequestError(normalizeError(body, response.status), body as PlatformEnvelope<unknown> | undefined, response.status);
  }

  if (response.status === 204) return undefined as T;
  const body = await response.json() as PlatformEnvelope<T> | T;
  if (body && typeof body === "object" && "error" in body && (body as PlatformEnvelope<T>).error) {
    const envelope = body as PlatformEnvelope<T>;
    throw new PlatformRequestError(envelope.error as PlatformError, envelope);
  }
  if (body && typeof body === "object" && "data" in body) {
    return (body as PlatformEnvelope<T>).data as T;
  }
  return body as T;
}
