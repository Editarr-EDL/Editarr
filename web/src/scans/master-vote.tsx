import { useState } from "react";
import { voteMasterFilter } from "../api/api-community-masters";
import type { MyVote, VoteValue } from "../api/api-community-master-types";
import { CommunityApiError } from "../api/api-community";
import { masterErrorText } from "./community-master-format";
import { notifyCommunityVoteChanged } from "./community-master-events";
import { VoteMenuButton } from "./vote-menu-button";

type Pending = { vote: VoteValue; operationId: string };

/**
 * A served filter's vote, sent immediately: the {@link VoteMenuButton} pill, then "Retry vote" after a
 * retryable failure (resending the same operation ID) and any error. A sent vote is announced so other
 * open views of the filter update.
 * @param linkId The master link voted through, or null when the filter has none (`unavailable` says why).
 * @param unavailable Why voting isn't possible here; the pill stays visible, showing `myVote`.
 */
export function MasterVote({ linkId, filterId, name, myVote, unavailable = null, onVoted }: {
  linkId: string | null; filterId: string; name: string; myVote: MyVote; unavailable?: string | null;
  onVoted?: (vote: MyVote) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = async (intent: Pending, retry: boolean) => {
    if (!linkId) return;
    setBusy(true);
    setError(null);
    try {
      await voteMasterFilter(linkId, filterId, intent, retry);
      setPending(null);
      const vote = intent.vote === "none" ? null : intent.vote;
      onVoted?.(vote);
      notifyCommunityVoteChanged({ linkId, filterId, myVote: vote });
    } catch (reason) {
      setError(masterErrorText(reason));
      setPending(reason instanceof CommunityApiError && reason.retryable ? intent : null);
    } finally { setBusy(false); }
  };

  return <span className="master-votes">
    <VoteMenuButton value={myVote} name={name} busy={busy}
      unavailable={unavailable ?? (pending && !busy ? "Retry the vote that didn’t go through first." : null)}
      onChoose={(vote) => void send({ vote, operationId: `vote-${crypto.randomUUID()}` }, false)} />
    {pending && <button type="button" className="btn-secondary compact-button" disabled={busy}
      onClick={() => void send(pending, true)}>Retry vote</button>}
    {error && <span className="master-inline-error" role="alert">{error}</span>}
  </span>;
}
