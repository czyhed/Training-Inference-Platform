"use client";

import {
  youyunMachineInventoryClass,
  youyunMachineInventoryLabel,
} from "../lib/youyun-location";

type InventoryMachine = {
  soldOut: boolean;
  inventoryStatus?: "available" | "sold_out" | "unknown" | "error";
  inventoryMessage?: string;
};

/** Compact stock affordance for GPU cards; the full reason remains available to AT/title. */
export function YouyunInventoryIndicator({ machine }: { machine: InventoryMachine }) {
  const status = youyunMachineInventoryClass(machine);
  const label = youyunMachineInventoryLabel(machine);
  return (
    <span className={`youyun-inventory-indicator ${status}`} title={label} aria-label={label}>
      <i aria-hidden="true" />
      <i aria-hidden="true" />
      <i aria-hidden="true" />
    </span>
  );
}
