/** Small inline SVG charts. Geometry uses SVG attributes only (the operator CSP forbids inline styles). */
export interface Point { t: number; v: number; v2?: number | null }

function path(values: number[], width: number, height: number, low: number, high: number, pad = 2) {
  const span = high - low || 1, last = Math.max(1, values.length - 1);
  return values.map((value, index) => `${(index / last * width).toFixed(1)},${(height - pad - (value - low) / span * (height - pad * 2)).toFixed(1)}`).join(" ");
}

/** A decorative 84×28 sparkline; pair it with visible numbers. `tone` picks the stroke colour class. */
export function Sparkline({ values, tone = "blue" }: { values: number[]; tone?: "blue" | "teal" | "warn" }) {
  if (values.length < 2) return <svg className="spark" viewBox="0 0 84 28" aria-hidden="true" />;
  const low = Math.min(...values), high = Math.max(...values), points = path(values, 84, 28, low, high);
  const lastY = points.split(" ").pop()!.split(",")[1];
  return <svg className={`spark tone-${tone}`} viewBox="0 0 84 28" aria-hidden="true" focusable="false">
    <polygon className="area" points={`0,28 ${points} 84,28`} /><polyline className="line" points={points} />
    <circle className="dot-end" cx="84" cy={lastY} r="2.4" /></svg>;
}

/** A horizontal proportion bar (0–1), e.g. leaderboard share or storage used. */
export function Meter({ value, tone = "blue", className = "meter" }: { value: number; tone?: "blue" | "teal"; className?: string }) {
  const width = Math.max(0, Math.min(1, value)) * 100;
  return <svg className={`${className} tone-${tone}`} viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    <rect className="track" width="100" height="6" rx="3" /><rect className="fill" width={width.toFixed(1)} height="6" rx="3" /></svg>;
}

const dayLabel = (ms: number) => new Date(ms).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
/**
 * A trend chart with an accessible summary: `summary` becomes the image label and a visually hidden description lists
 * the range. `kind` draws a line (with optional second series `v2`) or daily bars; `limit` draws a dashed quota line.
 */
export function TrendChart({ points, kind = "line", tone = "blue", tone2 = "teal", summary, format, limit, highlight, fromZero = true }: {
  points: Point[]; kind?: "line" | "bars"; tone?: string; tone2?: string; summary: string; format: (value: number) => string;
  limit?: number | null; highlight?: (point: Point) => boolean; fromZero?: boolean }) {
  const W = 300, H = 110;
  const values = points.map(point => point.v), second = points.map(point => point.v2 ?? null).filter((value): value is number => value !== null);
  const top = Math.max(1, ...values, ...second, limit ?? 0);
  const floor = fromZero || kind === "bars" || !values.length ? 0 : Math.min(...values, ...second) * 0.96;
  const high = top + (top - floor) * 0.08, low = floor;
  const grid = [0.25, 0.5, 0.75].map(fraction => <line key={fraction} className="gridline" x1="0" x2={W} y1={H * fraction} y2={H * fraction} />);
  let body;
  if (!points.length) body = null;
  else if (kind === "bars") {
    const step = W / points.length;
    body = points.map((point, index) => { const height = point.v / high * (H - 4);
      return <rect key={point.t} className={`cbar tone-${highlight?.(point) ? "bad" : tone}`} x={(index * step + step * 0.18).toFixed(1)}
        y={(H - height).toFixed(1)} width={(step * 0.64).toFixed(1)} height={height.toFixed(1)} rx="1.5" />; });
  } else {
    const main = path(values, W, H, low, high, 4);
    body = <>
      <polygon className={`area tone-${tone}`} points={`0,${H} ${main} ${W},${H}`} />
      <polyline className={`line tone-${tone}`} points={main} />
      {second.length === points.length && <polyline className={`line tone-${tone2}`} points={path(second, W, H, low, high, 4)} />}
    </>;
  }
  const limitY = limit ? (H - 4 - (limit - low) / (high - low) * (H - 8)).toFixed(1) : null;
  const first = points[0], last = points[points.length - 1];
  const description = points.length ? `From ${dayLabel(first.t)} to ${dayLabel(last.t)}: lowest ${format(Math.min(...values))}, highest ${format(Math.max(...values))}, latest ${format(last.v)}.`
    : "No data points recorded for this period.";
  return <div className="chart-wrap">
    <span className="chart-y" aria-hidden="true">{format(top)}</span>
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${summary}. ${description}`}>
      {grid}{body}{limitY && <line className="limit" x1="0" x2={W} y1={limitY} y2={limitY} />}</svg>
    <div className="chart-x" aria-hidden="true">{first && <><span>{dayLabel(first.t)}</span>
      <span>{dayLabel(points[Math.floor(points.length / 2)].t)}</span><span>{dayLabel(last.t)}</span></>}</div>
  </div>;
}
