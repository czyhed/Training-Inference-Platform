import assert from "node:assert/strict";
import test from "node:test";

import { mergeTrainingDetail } from "./training-detail-state.ts";

test("a delayed detail response does not erase Loss received from the live stream", () => {
  const fetched = {
    id: "ft-live",
    state: "running",
    progress: 40,
    currentStep: "训练中",
    logs: ["fetched snapshot"],
    lossHistory: [],
  };
  const live = {
    ...fetched,
    logs: ["live log"],
    lossHistory: [{ step: 10, trainLoss: 1.23 }],
  };

  const merged = mergeTrainingDetail(live, fetched, true);

  assert.deepEqual(merged.logs, live.logs);
  assert.deepEqual(merged.lossHistory, live.lossHistory);
});
