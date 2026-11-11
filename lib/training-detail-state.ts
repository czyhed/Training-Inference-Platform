import type { TrainingTask } from "./types";

/** Merge a detail response without discarding logs received from the live stream. */
export function mergeTrainingDetail(
  current: TrainingTask | null,
  fetched: TrainingTask,
  preserveLiveLogs: boolean,
): TrainingTask {
  if (!current || current.id !== fetched.id) return fetched;
  return {
    ...fetched,
    logs: preserveLiveLogs && current.logs?.length ? current.logs : fetched.logs,
    lossHistory: current.lossHistory?.length ? current.lossHistory : fetched.lossHistory,
  };
}
