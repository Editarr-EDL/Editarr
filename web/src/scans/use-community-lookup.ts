import { useEffect, useRef, useState } from "react";
import { CommunityApiError } from "../api/api-community";
import { LOOKUP_POLICY, lookupCommunityFilters, type LookupError, type LookupInput, type LookupResponse,
  type RemoteFilterCandidate } from "../api/api-community-lookup";
import type { FilterCandidate } from "./use-find-filters";
import { useCan } from "../auth/auth-gate";
import { useCommunityLifetime } from "../settings/community/community-access";

type LookupState = {
  key: string; status: "idle" | "loading" | "ready" | "error" | "disabled" | "cancelled";
  response: LookupResponse | null; candidates: RemoteFilterCandidate[]; error: LookupError | null; stale: boolean;
};
const empty: LookupState = { key: "", status: "idle", response: null, candidates: [], error: null, stale: false };

export function remoteFilterDisplay(candidate: RemoteFilterCandidate): FilterCandidate {
  return {
    id: `community:${candidate.revisionId}`, label: candidate.summary.name || candidate.revisionId,
    origin: candidate.summary.kind, alreadyAssociated: false,
    options: `${candidate.summary.total} filters · ${candidate.attribution.displayName}`,
    matchDescription: `Video: ${candidate.compatibility.video.band}; Audio: ${candidate.compatibility.audio.band}`,
  };
}

/** One explicit target generation; no library polling or automatic retries/enrollment. */
export function useCommunityLookup(input: LookupInput | null) {
  const allowed = useCan("community.search");
  const lifetime = useCommunityLifetime();
  const key = JSON.stringify([LOOKUP_POLICY, input]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const [state, setState] = useState<LookupState>(empty);
  const stateRef = useRef(state);
  stateRef.current = state;
  useEffect(() => {
    generation.current++;
    pending.current?.abort();
    setState({ ...empty, key });
    return () => { generation.current++; pending.current?.abort(); };
  }, [key]);
  const visible = state.key === key ? state : { ...empty, key };

  const cancel = () => {
    generation.current++;
    pending.current?.abort();
    setState((previous) => ({ ...previous, status: "cancelled", stale: !!previous.response }));
  };
  const run = async (refresh = false, more = false) => {
    if (!allowed || !lifetime.current() || !input) return;
    const prior = stateRef.current.key === key ? stateRef.current : { ...empty, key };
    const cursor = more ? prior.response?.page?.nextCursor : null;
    if (more && (!cursor || prior.stale || prior.status === "loading")) return;
    const token = ++generation.current;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    setState({ ...prior, key, status: "loading", error: null });
    try {
      const response = await lookupCommunityFilters(input, { signal: controller.signal, refresh, cursor,
        targetKey: more ? prior.response?.targetKey : null });
      if (!lifetime.current() || token !== generation.current || currentKey.current !== key || controller.signal.aborted) return;
      const error = response.availabilityError || response.pageError;
      const candidates = response.page
        ? (more ? [...prior.candidates, ...response.page.candidates.filter((candidate) =>
          !prior.candidates.some((existing) => existing.revisionId === candidate.revisionId))] : response.page.candidates)
        : prior.candidates;
      setState({ key, status: error ? "error" : "ready", response, candidates, error,
        stale: !!error && prior.candidates.length > 0 });
    } catch (reason) {
      if (!lifetime.current() || token !== generation.current || currentKey.current !== key || controller.signal.aborted) return;
      const error = reason instanceof CommunityApiError ? { code: reason.code, retryable: reason.retryable }
        : { code: "network_unavailable", retryable: true };
      const invalidates = ["not_found", "capability_denied", "target_changed", "file_changed", "audio_selection_stale", "unsupported_policy"].includes(error.code);
      setState({ ...(invalidates ? { ...empty, key } : prior), key,
        status: ["not_configured", "not_connected"].includes(error.code) ? "disabled" : "error",
        error, stale: !invalidates && !!prior.response });
    }
  };
  return { ...visible, displayCandidates: visible.candidates.map(remoteFilterDisplay),
    start: () => run(), refresh: () => run(true), more: () => run(false, true), cancel };
}