import { useEffect, useId, useRef, useState } from "react";
import { CommunityApiError } from "../api/api-community";
import { readCommunityFeedback, sendCommunityVote, type Feedback, type Vote, type VoteIntent } from "../api/api-community-votes";
import { useCan } from "../auth/auth-gate";
import { useCommunityLifetime } from "../settings/community/community-access";

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

export function CommunityFeedback({ revisionId, disabled, refreshKey, authorId }: {
  revisionId: string; disabled: boolean; refreshKey: string; authorId?: string;
}) {
  const allowed = useCan("community.feedback.submit");
  const lifetime = useCommunityLifetime();
  const group = useId();
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [pending, setPending] = useState<VoteIntent | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    generation.current++;
    controller.current?.abort();
    setFeedback(null); setPending(null); setError(null); setBusy(false);
    return () => { generation.current++; controller.current?.abort(); };
  }, [revisionId, disabled, refreshKey]);

  const read = async (signal: AbortSignal, current: number) => {
    const result = await readCommunityFeedback(revisionId, signal);
    if (!lifetime.current() || signal.aborted || current !== generation.current) return;
    setFeedback(result); setPending(result.pending); setError(result.error?.code || null);
  };
  const act = async (intent?: VoteIntent, retry = false) => {
    if (!allowed || !lifetime.current() || disabled || busy) return;
    const current = ++generation.current;
    controller.current?.abort();
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError(null); setFeedback(null);
    if (intent) setPending(intent);
    try {
      if (intent) {
        await sendCommunityVote(revisionId, intent, request.signal, retry);
        if (lifetime.current() && current === generation.current) setPending(null);
      }
      if (lifetime.current() && !request.signal.aborted && current === generation.current) await read(request.signal, current);
    } catch (failure) {
      if (lifetime.current() && current === generation.current && !request.signal.aborted) {
        if (failure instanceof CommunityApiError && [403, 404].includes(failure.status)) { setFeedback(null); setPending(null); }
        setError(failure instanceof CommunityApiError ? failure.code : "network_unavailable");
      }
    } finally {
      if (lifetime.current() && current === generation.current) setBusy(false);
    }
  };
  const vote = (value: Vote) => void act({ operationId: `vote-${crypto.randomUUID()}`, vote: value, state: "pending" });
  if (!allowed) return null;
  return <section className="community-feedback" aria-label={`Feedback for revision ${revisionId}`}>
    <div className="button-row">
      <button className="btn-secondary" type="button" disabled={disabled || busy} onClick={() => void act()}>Check feedback</button>
      <button className="btn-secondary" type="button" disabled title="Report submission awaits the moderation service">Report abuse</button>
    </div>
    <fieldset disabled={disabled || busy || !!pending || !feedback?.current?.eligible}>
      <legend>Your vote</legend>
      {(["like", "dislike", "neutral"] as const).map((value) => <label key={value}>
        <input type="radio" name={group} value={value} checked={(pending?.vote || feedback?.current?.vote) === value}
          onChange={() => vote(value)} />{value === "like" ? "Like" : value === "dislike" ? "Dislike" : "Neutral"}
      </label>)}
    </fieldset>
    <p role="status" aria-live="polite">{busy ? "Checking community feedback..." : pending
      ? `Pending ${pending.vote} change; current vote is not confirmed by this intent.`
      : feedback?.current ? `Current vote: ${feedback.current.vote}.` : "Current vote unavailable."}</p>
    {feedback?.current && !feedback.current.eligible && <p>{messages[authorId === feedback.scope.contributorId
      ? "self_vote" : feedback.current.reason || "import_required"]}</p>}
    {feedback?.totals && <p>Current totals: {feedback.totals.likes} likes, {feedback.totals.dislikes} dislikes. Catalog ranking is a snapshot.</p>}
    {error && <p role="alert">{messages[error] || "Fresh feedback is unavailable. No vote change has been discarded."}</p>}
    {pending && <button className="btn-secondary" type="button" disabled={disabled || busy}
      onClick={() => void act(pending, true)}>Retry saved vote</button>}
  </section>;
}