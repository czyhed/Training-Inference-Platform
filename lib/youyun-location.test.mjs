import assert from "node:assert/strict";
import test from "node:test";

import {
  friendlyYouyunError,
  selectYouyunZone,
  youyunMachineInventoryLabel,
  youyunMachineInventoryClass,
  youyunMachineIsUnavailable,
  youyunRegionLabel,
} from "./youyun-location.ts";

const zones = [
  { region: "cn-bj2", zone: "cn-bj2-01", is_pod: false },
  { region: "cn-bj2", zone: "cn-bj2-03", is_pod: true },
  { region: "cn-sh2", zone: "cn-sh2-01", is_pod: false },
  { region: "cn-sh2", zone: "cn-sh2-02", is_pod: true },
];

test("selects a Pod zone inside the chosen region", () => {
  assert.equal(selectYouyunZone(zones, "cn-sh2", "pod"), "cn-sh2-02");
});

test("does not mix UHost and Pod zones", () => {
  assert.equal(selectYouyunZone(zones, "cn-bj2", "uhost"), "cn-bj2-01");
  assert.equal(selectYouyunZone(zones, "cn-none", "pod"), "");
});

test("formats region labels and actionable provider errors", () => {
  assert.equal(youyunRegionLabel("cn-wlcb"), "华北二区");
  assert.equal(youyunRegionLabel("cn-unknown"), "cn-unknown");
  assert.equal(friendlyYouyunError("Resource capacity is unavailable"), "当前地域暂无该 GPU 可用库存，请切换地域或 GPU。");
});

test("does not treat a missing inventory status as sold out", () => {
  const machine = { name: "5090", soldOut: false };
  assert.equal(youyunMachineIsUnavailable(machine), false);
  assert.equal(youyunMachineInventoryClass(machine), "tag-pending");
  assert.equal(youyunMachineInventoryLabel(machine), "机型可用，组合库存待确认");
});

test("treats an explicit sold-out status as unavailable", () => {
  const machine = { name: "4090", soldOut: false, inventoryStatus: "sold_out" };
  assert.equal(youyunMachineIsUnavailable(machine), true);
  assert.equal(youyunMachineInventoryClass(machine), "tag-off");
});

test("keeps price failures distinct from a confirmed inventory failure", () => {
  assert.equal(
    friendlyYouyunError("所选优云 GPU 规格当前无库存，请刷新后重试。", "price"),
    "库存已确认，但优云价格接口暂不可用，请重试。",
  );
});
