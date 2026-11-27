import { useState } from "react";
import type { JellyfinMediaItem } from "../api/api-media-types";

export function formatRating(item: JellyfinMediaItem): string {
  return item.official_rating || "—";
}

export function formatMediaDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function formatMcfDuration(seconds: number): string {
  const totalMilliseconds = Math.max(0, Math.round(seconds * 1_000));
  const hours = Math.floor(totalMilliseconds / 3_600_000);
  const minutes = Math.floor((totalMilliseconds % 3_600_000) / 60_000);
  const wholeSeconds = Math.floor((totalMilliseconds % 60_000) / 1_000);
  const milliseconds = totalMilliseconds % 1_000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

export function MediaDuration({ seconds }: { seconds: number | null }) {
  const [showExact, setShowExact] = useState(false);
  if (seconds === null || seconds === undefined) return <>—</>;
  const exactDuration = formatMcfDuration(seconds);
  const compactDuration = formatMediaDuration(seconds);
  return <button type="button" className="media-duration" aria-label={`${showExact ? "Show compact length" : "Show exact length"}: ${showExact ? compactDuration : exactDuration}`} aria-pressed={showExact} onClick={() => setShowExact((visible) => !visible)}>{showExact ? exactDuration : compactDuration}</button>;
}

