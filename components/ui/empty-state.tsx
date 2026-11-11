import type { ReactNode } from "react";
import type { EmptyStateKind } from "./ui-types";

export interface EmptyStateProps {
  kind: EmptyStateKind;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ kind, title, description, action }: EmptyStateProps): ReactNode {
  return (
    <div className={`ui-empty-state ui-empty-${kind}`} aria-live="polite">
      <strong>{title}</strong>
      {description ? <span>{description}</span> : null}
      {action ? <div className="ui-empty-state-action">{action}</div> : null}
    </div>
  );
}
