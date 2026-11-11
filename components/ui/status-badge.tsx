import type { ReactNode } from "react";
import type { StatusTone } from "./ui-types";

export interface StatusBadgeProps {
  tone: StatusTone;
  label: string;
  detail?: string;
}

export function StatusBadge({ tone, label, detail }: StatusBadgeProps): ReactNode {
  return (
    <span className={`ui-status-badge ui-status-${tone}`} role="status">
      <span className="ui-status-dot" aria-hidden="true" />
      <span>{label}</span>
      {detail ? <small>{detail}</small> : null}
    </span>
  );
}
