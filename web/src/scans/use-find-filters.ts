import { useEffect, useRef, useState } from "react";
import { associateFilterCandidates, findFilterCandidates } from "../api/api-scans";
import type { FilterCandidateSearchResult } from "../api/api-scan-types";
import { scanLabel, scanOriginLabel } from "../shared/display";
import { scanOptionsSummary } from "./scan-components";
import type { LookupInput, RemoteFilterCandidate } from "../api/api-community-lookup";
import { remoteFilterDisplay, useCommunityLookup } from "./use-community-lookup";

export type LocalFilterTarget = Parameters<typeof findFilterCandidates>[0];

export interface FilterCandidate {
  id: string;
  label: string;
  origin: string;
  options: string;
  matchDescription: string;
  alreadyAssociated: boolean;
}

export type FilterResult =
  | { source: "local"; candidate: FilterCandidate; match: FilterCandidateSearchResult["matches"][number] }
  | { source: "community"; candidate: FilterCandidate; remote: RemoteFilterCandidate };

/** Compose explicit local association and independent, unselected remote discovery. */
export function useFindFilters({
  target,
  onMessage,
  onAssociated,
  requireVerification = false,
  communityInput = null,
}: {
  target: LocalFilterTarget;
  onMessage: (message: string | null) => void;
  onAssociated: () => Promise<void>;
  requireVerification?: boolean;
  communityInput?: LookupInput | null;
}) {
  const community = useCommunityLookup(communityInput);
  const key = JSON.stringify(target);
  const currentKey = useRef(key);
  currentKey.current = key;
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const [finding, setFinding] = useState(false);
  const [result, setResult] = useState<FilterCandidateSearchResult | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [associating, setAssociating] = useState(false);
  useEffect(() => {
    generation.current++;
    pending.current?.abort();
    setResult(null);
    setSelectedIds([]);
    setFinding(false);
    setAssociating(false);
    return () => { generation.current++; pending.current?.abort(); };
  }, [key]);

  const find = async (includeCommunity = true) => {
    if (!target.item_id) {
      onMessage("Choose a media item for filter matching.");
      return;
    }
    setFinding(true);
    onMessage(null);
    const token = ++generation.current;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    if (includeCommunity) void community.start();
    try {
      const next = await findFilterCandidates(target, controller.signal);
      if (token !== generation.current || currentKey.current !== key || controller.signal.aborted) return;
      setResult(next);
      setSelectedIds(next.matches.filter((match) => !match.already_associated).map((match) => match.scan.id));
    } catch (error) {
      if (token !== generation.current || currentKey.current !== key || controller.signal.aborted) return;
      onMessage(error instanceof Error ? error.message : "Unable to find matching filters.");
    } finally {
      if (token === generation.current && currentKey.current === key) setFinding(false);
    }
  };

  const associate = async () => {
    if (!result || !selectedIds.length || associating) return;
    const token = generation.current;
    setAssociating(true);
    try {
      await associateFilterCandidates(result.media_item_id, selectedIds);
      if (currentKey.current !== key || token !== generation.current) return;
      setResult(null);
      community.cancel();
      onMessage(`Associated ${selectedIds.length} ${selectedIds.length === 1 ? "scan" : "scans"}.${requireVerification ? " Check their timing under Scans before using them in a review." : ""}`);
      await onAssociated();
    } catch (error) {
      if (currentKey.current !== key || token !== generation.current) return;
      onMessage(error instanceof Error ? error.message : "Unable to associate filters.");
    } finally {
      if (currentKey.current === key && token === generation.current) setAssociating(false);
    }
  };

  const candidates: FilterCandidate[] = (result?.matches || []).map((match) => ({
    id: match.scan.id,
    label: scanLabel(match.scan),
    origin: scanOriginLabel(match.scan),
    options: scanOptionsSummary(match.scan),
    matchDescription: match.already_associated ? "Already associated" : `Found by ${
      match.match_reason === "quick_hash" ? "quick hash + duration"
        : match.match_reason === "scene_release" ? "scene name + release group"
          : match.match_reason === "format" ? "format + duration" : "file name + duration"
    }`,
    alreadyAssociated: match.already_associated,
  }));

  const results: FilterResult[] = [
    ...candidates.map((candidate, index) => ({ source: "local" as const, candidate, match: result!.matches[index] })),
    ...community.candidates.map((remote) => ({ source: "community" as const, candidate: remoteFilterDisplay(remote), remote })),
  ];

  return {
    community,
    results,
    finding,
    open: result !== null || (communityInput !== null && community.status !== "idle" && community.status !== "cancelled"),
    candidates,
    selectedIds,
    associating,
    find,
    associate,
    close: () => {
      if (associating) return;
      generation.current++;
      pending.current?.abort();
      community.cancel();
      setFinding(false);
      setResult(null);
    },
    select: (id: string, checked: boolean) => {
      if (associating || !candidates.some((candidate) => candidate.id === id && !candidate.alreadyAssociated)) return;
      setSelectedIds((current) => checked ? [...new Set([...current, id])] : current.filter((scanId) => scanId !== id));
    },
  };
}