import { useId } from "react";
import type { ReactNode } from "react";

export interface SectionPanelProps {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function SectionPanel({ title, description, actions, className, children }: SectionPanelProps): ReactNode {
  const titleId = useId();
  return (
    <section className={`ui-section-panel${className ? ` ${className}` : ""}`} aria-labelledby={titleId}>
      <div className="ui-section-panel-head">
        <div>
          <h2 id={titleId}>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        {actions ? <div className="ui-section-panel-actions">{actions}</div> : null}
      </div>
      <div className="ui-section-panel-body">{children}</div>
    </section>
  );
}
