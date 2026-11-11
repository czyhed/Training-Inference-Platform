import assert from "node:assert/strict";
import test from "node:test";

import { loadYouyunSpecs } from "./youyun-location.ts";

test("explicit inventory refresh bypasses the cached capacity snapshot", async () => {
  let calls = 0;
  const request = async () => {
    calls += 1;
    return {
      region: "cn-wlcb",
      zone: "cn-wlcb-03",
      gpuType: "4090",
      instanceMode: "pod",
      machineTypes: [{ name: "4090", zone: "cn-wlcb-03", soldOut: false }],
      capacity: [{ gpu: 1, cpu: 16, memoryGb: 64, available: calls > 1 }],
    };
  };
  const selection = {
    gpuType: "4090",
    imageId: "train-img",
    region: "cn-wlcb",
    zone: "cn-wlcb-03",
    instanceMode: "pod",
    includeCommunity: false,
  };

  const first = await loadYouyunSpecs(request, selection, "training", () => true);
  const refreshed = await loadYouyunSpecs(
    request,
    selection,
    "training",
    () => true,
    { force: true },
  );

  assert.equal(first.capacity[0].available, false);
  assert.equal(refreshed.capacity[0].available, true);
  assert.equal(calls, 2);
});
