import { useEffect, useEffectEvent, useRef, useState, type RefObject } from "react";
import { CommunityApiError } from "../api/api-community";
import { discardSharingPreview, prepareSharingPreview, type SharingDraft, type SharingPreview, type SharingSource } from "../api/api-community-sharing";
import { useCommunityLifetime } from "../settings/community/community-access";

type Result = { draft: SharingDraft; attempt: number } & ({ value: SharingPreview } | { error: string });

export function sharingErrorCode(reason: unknown) {
  return reason instanceof CommunityApiError ? reason.code : "network_unavailable";
}

export function sharingAccessLost(reason: unknown) {
  return reason instanceof CommunityApiError && [403, 404].includes(reason.status);
}

export function useSharingSummary(
  source: SharingSource, draft: SharingDraft | null, accessLost: RefObject<boolean>, onAccessLost: (code: string) => void,
) {
  const lifetime = useCommunityLifetime();
  const handleAccessLost = useEffectEvent(onAccessLost);
  const { kind, id } = source;
  const [result, setResult] = useState<Result | null>(null);
  const [attempt, setAttempt] = useState(0);
  const queue = useRef(Promise.resolve());
  const displayed = result?.draft === draft && result.attempt === attempt ? result : null;

  useEffect(() => {
    if (!draft) return;
    const signal = lifetime.signal();
    let superseded = false;
    let retained: string | null = null;
    const current = () => !signal.aborted && !accessLost.current && lifetime.current();
    const discard = async (previewId: string) => {
      if (!current()) return;
      try { await discardSharingPreview(previewId, signal); }
      catch { /* Unreleased summaries expire on the server. */ }
    };
    // Serialize preparation and cleanup so discarded drafts do not fill the server's preview store.
    const timer = window.setTimeout(() => {
      queue.current = queue.current.then(async () => {
        if (superseded || !current()) return;
        try {
          const value = await prepareSharingPreview({ kind, id }, draft, signal);
          if (!current()) return;
          if (superseded) {
            await discard(value.previewId);
            return;
          }
          retained = value.previewId;
          setResult({ draft, attempt, value });
        } catch (reason) {
          if (!superseded && current()) {
            const code = sharingErrorCode(reason);
            if (sharingAccessLost(reason)) handleAccessLost(code);
            setResult({ draft, attempt, error: code });
          }
        }
      });
    }, 200);
    return () => {
      superseded = true;
      window.clearTimeout(timer);
      if (retained && current()) {
        const previewId = retained;
        queue.current = queue.current.then(() => discard(previewId));
      }
    };
  }, [kind, id, draft, attempt, accessLost]);

  return {
    preview: displayed && "value" in displayed ? displayed.value : null,
    error: displayed && "error" in displayed ? displayed.error : null,
    loading: !!draft && !displayed,
    retry: () => setAttempt((value) => value + 1),
  };
}
