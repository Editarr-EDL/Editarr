/** Format seconds with the requested decimal precision, carrying rounded values across minutes. */
export function formatReviewTimestamp(seconds: number | null, decimalPlaces: 2 | 3): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "";
  const unitsPerSecond = 10 ** decimalPlaces;
  const totalUnits = Math.round(seconds * unitsPerSecond);
  const wholeSeconds = Math.floor(totalUnits / unitsPerSecond);
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const parts = [hours, minutes, wholeSeconds % 60].map((part) => String(part).padStart(2, "0"));
  return `${parts.join(":")}.${String(totalUnits % unitsPerSecond).padStart(decimalPlaces, "0")}`;
}

/** Parse a non-negative clock time with two or more hour digits and fixed decimal precision. */
export function parseReviewTimestamp(value: string, decimalPlaces: 2 | 3): number {
  const match = /^(\d{2,}):([0-5]\d):([0-5]\d)\.(\d{2,3})$/.exec(value);
  if (!match || match[4].length !== decimalPlaces) return Number.NaN;
  const [, hours, minutes, seconds, decimal] = match;
  const parsed = Number(hours) * 3600 + Number(minutes) * 60 + Number(seconds) + Number(decimal) / 10 ** decimalPlaces;
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}
