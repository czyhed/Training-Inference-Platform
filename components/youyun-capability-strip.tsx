"use client";

import {
  youyunZoneCapabilityLabels,
  type YouyunLocationOption,
  type YouyunZone,
} from "../lib/youyun-location";

type CapabilityZone = YouyunZone | YouyunLocationOption;

export function YouyunCapabilityStrip({ zone }: { zone?: CapabilityZone | null }) {
  if (!zone) return null;
  return (
    <div className="youyun-zone-capability-strip" aria-label="当前可用区能力">
      {youyunZoneCapabilityLabels(zone).map((label) => (
        <span key={label} className="youyun-zone-capability">{label}</span>
      ))}
      <a
        href="https://www.compshare.cn/docs/operation/introduce/region"
        target="_blank"
        rel="noreferrer"
      >
        查看可用区说明
      </a>
    </div>
  );
}
