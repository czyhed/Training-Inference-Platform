export type Modality = "nlp" | "vision" | "image-generation";

export type ResourceState =
  | "none"
  | "provisioning"
  | "allocated"
  | "connected"
  | "releasing"
  | "released";

export type CleanupState =
  | "not_required"
  | "pending"
  | "running"
  | "released"
  | "failed";

export type PlatformTaskSummary = {
  id: string;
  modality: Modality;
  name: string;
  state: string;
  phase: string;
  progress: number | null;
  resourceState: ResourceState;
  cleanupState: CleanupState;
  failureCode: string | null;
  updatedAt: string;
};

export type PlatformError = {
  code: string;
  message: string;
  details: Array<Record<string, unknown>>;
  retryable: boolean;
};

export type PlatformEnvelope<T> = {
  requestId?: string;
  traceId?: string;
  data: T | null;
  error: PlatformError | null;
  meta?: { timestamp?: string; apiVersion?: string };
};
