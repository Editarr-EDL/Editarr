import type { ModelOption } from "./model-catalog";

/** Recommended or Default, followed by any `extra` badges, for the summary card and picker rows. */
export function ModelBadges({ id, option, extra = [], children }: {
  id?: string;
  option: ModelOption;
  extra?: string[];
  children?: React.ReactNode;
}) {
  return <span id={id} className="model-picker-badges">
    {option.recommended && <span className="model-picker-badge" data-kind="recommended">Recommended</span>}
    {option.isDefault && !option.recommended && <span className="model-picker-badge">Default</span>}
    {extra.map((badge) => <span key={badge} className="model-picker-badge" data-kind="info">{badge}</span>)}
    {children}
  </span>;
}
