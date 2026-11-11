"use client";

import type { YouyunZone } from "../lib/youyun-location";
import {
  youyunRegionLabel,
  youyunZoneCapabilityLabels,
  youyunZoneLabel,
  youyunZoneModeLabel,
} from "../lib/youyun-location";

export function YouyunZonePicker({
  zones,
  selectedZone,
  onSelect,
  hint,
}: {
  zones: YouyunZone[];
  selectedZone: string;
  onSelect: (zone: YouyunZone) => void;
  hint?: string;
}) {
  const selected = zones.find((zone) => zone.zone === selectedZone) ?? zones[0];
  return (
    <div className="youyun-location-picker">
      <span className="field-label">可用区</span>
      <div className="youyun-region-list" aria-label="优云可用区列表">
        {zones.map((zone) => (
          <button
            type="button"
            key={zone.zone}
            className={`youyun-region-option${zone.zone === selectedZone ? " selected" : ""}`}
            title={`${youyunRegionLabel(zone.region)} · ${zone.zone} · ${youyunZoneModeLabel(zone)}`}
            aria-pressed={zone.zone === selectedZone}
            onClick={() => onSelect(zone)}
          >
            <span className="youyun-zone-name">{youyunZoneLabel(zone)}</span>
            <span className="youyun-zone-mode">{youyunZoneModeLabel(zone)}</span>
          </button>
        ))}
      </div>
      {selected && (
        <div className="youyun-zone-capability-strip" aria-label="当前可用区能力">
          {youyunZoneCapabilityLabels(selected).map((label) => <span key={label} className="youyun-zone-capability">{label}</span>)}
          <a href="https://www.compshare.cn/docs/operation/introduce/region" target="_blank" rel="noreferrer">查看可用区说明</a>
        </div>
      )}
      <small className="vision-field-help">
        {zones.length
          ? hint ?? `${zones.length} 个可用区 · 来自优云实时目录`
          : "暂无可用区，请刷新重试。"}
      </small>
    </div>
  );
}
