import type { FilterStats } from "../../../api/api-community-operator-labels";
import { categoryClass, filterSummary, runtime } from "../shared/format";

const lanes = [["language", "Language"], ["violence", "Violence"], ["sexual", "Sexual content"], ["substances", "Substances"],
  ["frightening", "Frightening"], ["other", "Other"]] as const;
/** Opacity step 1–4 from net vote support, so well-supported filters read brighter. */
const support = (filter: FilterStats) => Math.max(1, Math.min(4, Math.round(((filter.upvotes ?? 0) - (filter.downvotes ?? 0) + 4) / 4)));

/**
 * Master filters on the version runtime, one lane per category. Each segment is a link that selects the filter;
 * `href` builds that link. The filter table below lists the same filters as text.
 */
export function RuntimeTimeline({ filters, runtimeMs, selected, href, revision }: { filters: FilterStats[]; runtimeMs: number | null;
  selected: string | null; href: (filterId: string) => string; revision: number }) {
  const end = Math.max(runtimeMs ?? 0, ...filters.map(filter => filter.endMs ?? 0), 1);
  const used = lanes.filter(([key]) => filters.some(filter => categoryClass(filter.category) === `cat-${key}`));
  const shown = used.length ? used : lanes.slice(0, 1);
  const reported = filters.some(filter => filter.openReports > 0);
  return <div className="card timeline">
    <div className="panel-head flat"><div><h2>Runtime</h2><p className="muted small">{filters.length} master filters at rev {revision} · brightness shows net
      vote support{reported && <> · <span className="warn-text">!</span> open report</>}</p></div></div>
    <div className="tl-legend" aria-hidden="true">{shown.map(([key, label]) => <span key={key}><i className={`dot cat-${key}`} />{label}</span>)}
      <span><i className="dot qt" />Quarantined</span></div>
    <div className="tl">
      {shown.map(([key, label]) => <div className="lane" key={key}><span>{label}</span>
        <svg className="track" viewBox="0 0 1000 24" preserveAspectRatio="none" role="group" aria-label={`${label} filters`}>
          <defs><pattern id={`qt-${key}`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="3" height="6" className="qt-a" /><rect x="3" width="3" height="6" className="qt-b" /></pattern></defs>
          {[1, 2, 3, 4, 5, 6, 7].map(step => <line key={step} className="tick" x1={step * 125} x2={step * 125} y1="0" y2="24" />)}
          {filters.filter(filter => categoryClass(filter.category) === `cat-${key}` && filter.startMs !== null).map(filter => {
            const start = (filter.startMs ?? 0) / end * 1000, width = Math.max(5, ((filter.endMs ?? filter.startMs ?? 0) - (filter.startMs ?? 0)) / end * 1000);
            return <a key={filter.filterId} href={href(filter.filterId)} aria-label={`${filterSummary(filter)}${filter.quarantined ? " (quarantined)" : ""}`}
              aria-current={filter.filterId === selected ? "true" : undefined}
              className={`seg-f ${filter.quarantined ? "qt" : `cat-${key} o${support(filter)}`}${filter.filterId === selected ? " sel" : ""}${filter.openReports ? " flag" : ""}`}>
              <rect x={start.toFixed(1)} y="3" width={width.toFixed(1)} height="18" rx="3" fill={filter.quarantined ? `url(#qt-${key})` : undefined} />
              <title>{filterSummary(filter)} · ▲{filter.upvotes ?? 0} ▼{filter.downvotes ?? 0}</title></a>;
          })}
        </svg></div>)}
      <div className="axis" aria-hidden="true"><span /><div>{[0, 0.25, 0.5, 0.75].map(step => <span key={step}>{runtime(end * step).replace(/:\d\d$/, "")}</span>)}
        <span>{runtime(end)}</span></div></div>
    </div>
    <div className="tl-foot"><span>Select a filter to open its card.</span>{runtimeMs === null && <span>Runtime unknown; scaled to the last filter.</span>}</div>
  </div>;
}
