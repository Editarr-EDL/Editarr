import { useState } from "react";
import { voteMasterFilter } from "../api/api-community-masters";
import type { MyVote, VoteValue } from "../api/api-community-master-types";
import { CommunityIcon } from "./community-icon";
import { masterErrorText } from "./community-master-format";
import { CommunityApiError } from "../api/api-community";

type Pending = { vote: VoteValue; operationId: string };

/**
 * Per-filter 👍/👎 buttons. Pressing the current vote clears it (`none`). A retryable failure keeps
 * the intent so "Retry vote" resends it with the same operation ID.
 */
export function MasterVoteButtons({ linkId, filterId, name, myVote, disabled, onVoted }: {
  linkId: string; filterId: string; name: string; myVote: MyVote; disabled?: boolean;
  onVoted: (vote: MyVote) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = async (intent: Pending, retry: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await voteMasterFilter(linkId, filterId, intent, retry);
      setPending(null);
      onVoted(intent.vote === "none" ? null : intent.vote);
    } catch (reason) {
      setError(masterErrorText(reason));
      setPending(reason instanceof CommunityApiError && reason.retryable ? intent : null);
    } finally { setBusy(false); }
  };
  const vote = (value: "up" | "down") =>
    void send({ vote: myVote === value ? "none" : value, operationId: `vote-${crypto.randomUUID()}` }, false);

  return <div className="master-votes">
    {(["up", "down"] as const).map((value) => {
      const label = value === "up" ? "Thumbs up" : "Thumbs down";
      return <button key={value} type="button" className="community-icon-button community-vote-button"
        aria-label={`${label} for ${name}`} aria-pressed={myVote === value} title={label}
        disabled={disabled || busy || pending !== null} onClick={() => vote(value)}>
        <CommunityIcon name={value === "up" ? "like" : "dislike"} />
      </button>;
    })}
    {pending && <button type="button" className="btn-secondary compact-button" disabled={busy}
      onClick={() => void send(pending, true)}>Retry vote</button>}
    {error && <span className="master-inline-error" role="alert">{error}</span>}
  </div>;
}
