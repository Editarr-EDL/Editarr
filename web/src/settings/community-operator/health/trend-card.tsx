import { readOperatorTrend, type Trend, type TrendMetric, type TrendPeriod } from "../../../api/api-community-operator-system";
import { bytes, compact, count, delta } from "../shared/format";
import { TrendChart } from "../shared/charts";
import { useOperatorData } from "../shared/operator-data";

const titles: Record<TrendMetric, string> = { db: "Database + WAL", storage: "Upload storage vs quota", denials: "Rate-limit denials",
  requests: "Requests", uploads_votes: "Uploads & votes", downloads: "Master downloads" };
const periodText: Record<TrendPeriod, string> = { "24h": "24 h", "7d": "7 d", "30d": "30 d", "90d": "90 d" };
const last = (trend: Trend) => trend.points[trend.points.length - 1] ?? null;

function Change({ now, before, label, invert = false }: { now: number | null; before: number | null; label: string; invert?: boolean }) {
  if (now === null) return null;
  const change = delta(now, before);
  if (!change) return null;
  const good = invert ? change.down : !change.down;
  return <span className={`dchip ${now === before ? "" : good ? "good" : "warn"}`}>{change.text} {label}</span>;
}

/** One metric's trend card: headline, change against the previous period, chart (with a text summary) and legend. */
export function TrendCard({ metric, period }: { metric: TrendMetric; period: TrendPeriod }) {
  const { data } = useOperatorData(`trend:${metric}:${period}`, signal => readOperatorTrend(metric, period, signal));
  const span = periodText[period];
  const observed = data?.provenance === "application_observed" ? "¹" : metric === "db" ? "²" : "";
  let head = <div className="big">—</div>, chart = null, legend = null, change = null;
  if (data) {
    const latest = last(data), first = data.points[0] ?? null;
    if (metric === "db") {
      head = <div className="big">{bytes(latest ? latest.v + (latest.v2 ?? 0) : null)} <small>{latest ? `${bytes(latest.v)} database · ${bytes(latest.v2 ?? 0)} WAL` : "no samples yet"}</small></div>;
      change = <Change now={latest ? latest.v : null} before={first?.v ?? null} label={`over ${span}`} invert />;
      chart = <TrendChart points={data.points.map(point => ({ t: point.t, v: point.v, v2: point.v + (point.v2 ?? 0) }))} tone="blue" tone2="teal"
        format={bytes} summary={`Database size over ${span}`} fromZero={false} />;
      legend = <><span><i className="tone-blue" />Database</span><span><i className="tone-teal" />Database + WAL (WAL resets at each checkpoint)</span></>;
    } else if (metric === "storage") {
      const quota = latest?.v2 ?? null, used = latest && quota ? latest.v / quota : null;
      head = <div className="big">{bytes(latest?.v ?? null)} <small>{quota ? `of ${bytes(quota)} · ${(used! * 100).toFixed(used! < 0.1 ? 1 : 0)}%` : "no samples yet"}</small></div>;
      change = <Change now={latest?.v ?? null} before={first?.v ?? null} label={`over ${span}`} invert />;
      chart = <TrendChart points={data.points.map(point => ({ t: point.t, v: point.v }))} tone="teal" limit={quota} format={bytes}
        summary={`Stored upload payloads over ${span}${quota ? ` against a ${bytes(quota)} quota` : ""}`} />;
      legend = <><span><i className="tone-teal" />Stored upload payloads</span><span><i className="tone-warn" />Quota</span></>;
    } else if (metric === "uploads_votes") {
      head = <></>;
      chart = <div className="split"><div><div className="big">{count(data.total ?? 0)} <small>uploads · {span}</small></div>
        <TrendChart points={data.points.map(point => ({ t: point.t, v: point.v }))} kind="bars" format={compact} summary={`Uploads per bucket over ${span}`} /></div>
        <div><div className="big">{count(data.total2 ?? 0)} <small>votes · {span}</small></div>
          <TrendChart points={data.points.map(point => ({ t: point.t, v: point.v2 ?? 0 }))} tone="ok" format={compact} summary={`Votes per bucket over ${span}`} /></div></div>;
      change = <Change now={data.total} before={data.previousTotal} label={`vs prior ${span}`} />;
      legend = <span>Exact counts from submissions and vote events</span>;
    } else {
      const unit = metric === "downloads" ? "receipts" : "requests";
      head = <div className="big">{count(data.total ?? 0)} <small>{unit} · {span}</small></div>;
      change = <Change now={data.total} before={data.previousTotal} label={`vs prior ${span}`} invert={metric === "denials"} />;
      chart = <TrendChart points={data.points.map(point => ({ t: point.t, v: point.v }))} kind={metric === "denials" ? "bars" : "line"}
        tone={metric === "denials" ? "warn" : metric === "downloads" ? "violet" : "blue"} format={compact} summary={`${titles[metric]} over ${span}`} />;
      legend = <span><i className={metric === "denials" ? "tone-warn" : metric === "downloads" ? "tone-violet" : "tone-blue"} />
        {metric === "denials" ? "Denied requests" : metric === "downloads" ? "Download receipts issued (exact)" : "Request units, all handlers"}</span>;
    }
  }
  return <article className="card trend" aria-labelledby={`trend-${metric}`}>
    <div className="th"><div><h3 id={`trend-${metric}`}>{titles[metric]}{observed}</h3>{head}</div>{change}</div>
    {chart}{data && !data.points.length && <p className="muted small">No samples recorded in this period yet.</p>}
    {legend && <div className="legend">{legend}</div>}
  </article>;
}
