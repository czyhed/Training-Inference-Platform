import { useId } from "react";
import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: string;
  description?: string;
  eyebrow?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, description, eyebrow, actions }: PageHeaderProps): ReactNode {
  const titleId = useId();
  return (
    <header className="ui-page-header" aria-labelledby={titleId}>
      <div className="ui-page-header-copy">
        {eyebrow ? <span className="ui-page-header-eyebrow">{eyebrow}</span> : null}
        <h1 id={titleId}>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="ui-page-header-actions">{actions}</div> : null}
    </header>
  );
}
