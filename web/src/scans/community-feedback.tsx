import { useEffect, useRef, useState } from "react";
import { CommunityApiError } from "../api/api-community";
import { readCommunityFeedback, sendCommunityVote, type Feedback, type Vote, type VoteIntent } from "../api/api-community-votes";
import { useCan } from "../auth/auth-gate";
import { useCommunityLifetime } from "../settings/community/community-access";
import { CommunityIcon } from "./community-icon";

const messages: Record<string, string> = {
  self_vote: "Authors cannot vote on their own revisions.",
  import_required: "An acknowledged import of this exact revision is required.",
  import_acknowledgment_required: "The import receipt has not been acknowledged yet.",
  membership_ineligible: "Current community membership is required.",
  contributor_suspended: "Community access is suspended.",
  not_connected: "Community account is not connected.",
  not_configured: "Community service is not configured.",
  rotation_pending: "Community key replacement is pending.",
  vote_pending: "A saved vote change is still pending.",
  vote_ineligible: "This revision is not eligible for your vote.",
  revision_not_available: "This revision is no longer available.",
};

export function CommunityFeedback({ revisionId, disabled, refreshKey, authorId, totals = { likes: 0, dislikes: 0 } }: {
  revisionId: string; disabled: boolean; refreshKey: string; authorId?: string;
  totals?: NonNullable<Feedback["totals"]>;
}) {
  const allowed = useCan("community.feedback.submit");
  const lifetime = useCommunityLifetime();
  const key = JSON.stringify([revisionId, refreshKey, disabled, allowed]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [pending, setPending] = useState<VoteIntent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    setFeedback(null);
    setPending(null);
    setError(null);
    setBusy(false);
    return () => controller.current?.abort();
  }, [key]);

  const act = async (requested?: Exclude<Vote, "neutral">, retry?: VoteIntent) => {
    if (!allowed || !lifetime.current() || disabled || (controller.current && !controller.current.signal.aborted)) return;
    const request = new AbortController();
    controller.current = request;
    const current = () => lifetime.current() && !request.signal.aborted && currentKey.current === key;
    setBusy(true);
    setError(null);
    const read = async () => {
      const result = await readCommunityFeedback(revisionId, request.signal);
      if (!current()) return null;
      setFeedback(result);
      setPending(result.pending);
      setError(result.error?.code || null);
      return result;
    };
    try {
      let intent = retry;
      if (requested) {
        // One click reads the authenticated state before deriving a toggle. A saved
        // unknown outcome must be reconciled through its original intent.
        const result = await read();
        if (!result?.current?.eligible || result.pending || result.scope.contributorId === authorId) return;
        intent = { operationId: `vote-${crypto.randomUUID()}`, vote: result.current.vote === requested ? "neutral" : requested, state: "pending" };
      }
      if (!intent || !current()) return;
      setPending(intent);
      await sendCommunityVote(revisionId, intent, request.signal, Boolean(retry));
      if (!current()) return;
      setPending(null);
      setFeedback(null);
      await read();
    } catch (failure) {
      if (!current()) return;
      setFeedback(null);
      if (failure instanceof CommunityApiError && [403, 404].includes(failure.status)) setPending(null);
      setError(failure instanceof CommunityApiError ? failure.code : "network_unavailable");
    } finally {
      if (controller.current === request) controller.current = null;
      if (current()) setBusy(false);
    }
  };

  const count = feedback?.totals ?? totals;
  const denial = feedback?.scope.contributorId && feedback.scope.contributorId === authorId ? "self_vote"
    : feedback?.current && !feedback.current.eligible ? feedback.current.reason : null;
  const message = error ? messages[error] || "Feedback is unavailable." : denial && messages[denial];
  return <div className="community-feedback">
    <div className="community-row-icons">
      {(["like", "dislike"] as const).map((value) => {
        const label = value === "like" ? "Thumbs up" : "Thumbs down";
        const tally = value === "like" ? count.likes : count.dislikes;
        return <button key={value} className="community-icon-button community-vote-button" type="button"
          aria-label={`${label} (${tally})`} aria-pressed={feedback?.current?.vote === value}
          title={allowed ? label : `${label} votes`} disabled={!allowed || disabled || busy || Boolean(pending)}
          onClick={() => void act(value)}><CommunityIcon name={value} /><span>({tally})</span></button>;
      })}
    </div>
    {message && <p className="community-action-message" role={error ? "alert" : "status"}>{message}</p>}
    {pending && <button className="btn-secondary community-vote-retry" type="button" disabled={disabled || busy}
      onClick={() => void act(undefined, pending)}>Retry saved vote</button>}
    <span className="visually-hidden" role="status">{busy ? "Updating feedback…" : pending ? `Pending ${pending.vote} change.`
      : feedback?.current ? `Current vote: ${feedback.current.vote}.` : ""}</span>
  </div>;
}
