import type { Presentation } from "../../network/presentation";

interface StateBadgeProps {
  presentation: Presentation;
  /** Optional accessible prefix, e.g. "Physical link". */
  prefix?: string;
}

/** Glyph + text label + tone class: status is never conveyed by colour alone. */
export function StateBadge({ presentation, prefix }: StateBadgeProps) {
  return (
    <span className={`state-badge state-badge--${presentation.tone}`}>
      <span aria-hidden="true" className="state-badge__glyph">
        {presentation.glyph}
      </span>
      {prefix ? <span className="visually-hidden">{prefix}: </span> : null}
      {presentation.label}
    </span>
  );
}
