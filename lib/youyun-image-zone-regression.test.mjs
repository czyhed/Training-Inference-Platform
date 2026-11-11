import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  youyunImageRuntimeDescription,
  youyunImageRuntimeLabel,
  youyunPreferredMode,
} from "./youyun-location.ts";

test("image runtime metadata stays tri-state while loading", () => {
  assert.equal(youyunImageRuntimeLabel(true), "容器镜像");
  assert.equal(youyunImageRuntimeLabel(false), "虚机镜像");
  assert.equal(youyunImageRuntimeLabel(undefined), "镜像类型加载中");
  assert.equal(youyunImageRuntimeDescription(null), "正在确认镜像与实例类型");
});

test("region clicks keep the selected image and instance mode", () => {
  const training = readFileSync("components/training-module.tsx", "utf8");
  const inference = readFileSync("components/inference-module.tsx", "utf8");
  assert.match(training, /youyunPreferredMode\(item, selectedYouyunImageContainer, selectedYouyunMode\)/);
  assert.match(inference, /youyunPreferredMode\(item, selectedYouyunImage\?\.container, selectedYouyunMode\)/);
  assert.match(training, /youyunImageRuntimeLabel\(selectedYouyunImageContainer\)/);
  assert.equal(youyunPreferredMode({ zone: "cn-wlcb-01", isPod: true, supportedModes: ["pod", "uhost"] }, false, "pod"), "uhost");
  assert.equal(youyunPreferredMode({ zone: "cn-wlcb-01", isPod: true, supportedModes: ["pod", "uhost"] }, true, "uhost"), "pod");
});
