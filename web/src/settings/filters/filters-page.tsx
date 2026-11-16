import { useId, useRef, type KeyboardEvent } from "react";

import { useCan } from "../../auth/auth-gate";
import { navigate } from "../../routes";
import { DefaultFilters } from "./default-filters";
import { MyFilters } from "./my-filters";
import { NewAccountMode } from "./new-account-mode";

export type FilterTab = "mine" | "server";

const tabs: { tab: FilterTab; label: string; route: string }[] = [
  { tab: "mine", label: "My filters", route: "settings/filters" },
  { tab: "server", label: "Server defaults", route: "settings/filters/server" },
];

/**
 * The Filters page. My filters is for everyone with personal filter capabilities and Server
 * defaults for admins. Both panels stay mounted so switching tabs keeps unsaved drafts. With only
 * one panel available there is no tab bar.
 */
export function FiltersPage({ tab }: { tab?: FilterTab }) {
  const mayEditMine = useCan("filters.preferences.edit.own");
  const mayChooseMine = useCan("filters.mode.choose.own");
  const mayEditDefaults = useCan("filters.instance_defaults.edit");
  const mayEditNewAccountMode = useCan("filters.instance_mode.edit");
  const available = { mine: mayEditMine || mayChooseMine, server: mayEditDefaults || mayEditNewAccountMode };
  const shown = tabs.filter(item => available[item.tab]);
  const active = shown.find(item => item.tab === tab)?.tab ?? shown[0]?.tab;
  const id = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + shown.length) % shown.length;
    buttons.current[next]?.focus();
    navigate(shown[next].route);
  };
  const panel = (which: FilterTab) => shown.length > 1
    ? { role: "tabpanel", id: `${id}-${which}`, "aria-labelledby": `${id}-${which}-tab`, hidden: active !== which }
    : {};

  return <>
    {shown.length > 1 && <div className="filter-tabs" role="tablist" aria-label="Filters">
      {shown.map((item, index) => <button key={item.tab} ref={node => { buttons.current[index] = node; }} type="button" role="tab"
        id={`${id}-${item.tab}-tab`} aria-label={item.label} aria-controls={`${id}-${item.tab}`} aria-selected={active === item.tab} tabIndex={active === item.tab ? 0 : -1}
        onClick={() => navigate(item.route)} onKeyDown={event => onKeyDown(event, index)}>
        {item.label}{item.tab === "server" && <span className="filter-tab-badge">Admin</span>}
      </button>)}
    </div>}
    {available.mine && <div className="filters-tab-panel" {...panel("mine")}><MyFilters /></div>}
    {available.server && <div className="filters-tab-panel" {...panel("server")}>
      {mayEditNewAccountMode && <NewAccountMode />}
      {mayEditDefaults && <DefaultFilters />}
    </div>}
  </>;
}
