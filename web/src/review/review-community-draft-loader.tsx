import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { fetchCommunityReviewDraft } from "../api/api-community-masters";
import type { MasterLinkSummary } from "../api/api-community-master-types";
import { masterErrorText } from "../scans/community-master-format";
import { loadCommunityDraft } from "./review-community";
import type { ReviewDraft } from "./review-draft";

/** Load normal editable draft rows for a newly selected community source. */
export function ReviewCommunityDraftLoader({ link, draftId, setDraft }: {
  link: MasterLinkSummary;
  draftId: string;
  setDraft: Dispatch<SetStateAction<ReviewDraft | null>>;
}) {
  const { linkId } = link;
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    fetchCommunityReviewDraft(linkId, request.signal).then(({ token, filters }) => {
      if (!request.signal.aborted) setDraft((draft) => draft?.id === draftId
        ? loadCommunityDraft(draft, linkId, token, filters) : draft);
    }).catch((reason: unknown) => {
      if (!request.signal.aborted) setError(masterErrorText(reason));
    });
    return () => request.abort();
  }, [linkId, draftId, setDraft, attempt]);
  return error ? <div className="button-row">
    <p role="alert">Community · {link.label}. {error}</p>
    <button type="button" className="btn-secondary compact-button" onClick={() => {
      setError(null);
      setAttempt((current) => current + 1);
    }}>Retry loading filters</button>
  </div> : <p className="muted-copy" role="status">Loading {link.label} filters…</p>;
}
