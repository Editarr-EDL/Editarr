import { useId, useState } from "react";
import { applyCommunityMaster, findCommunityVersions } from "../api/api-community-masters";
import type { FindLabel, FindResponse, FindResult, MasterTarget } from "../api/api-community-master-types";
import { useCan } from "../auth/auth-gate";
import { MatchPercent, UnconfirmedLabel } from "./community-master-badges";
import { masterErrorText, OTHER, type PickerValue } from "./community-master-format";
import { notifyCommunityMastersChanged } from "./community-master-panel";
import { VersionPicker } from "./version-picker";

function findLabel({ selection, otherText }: PickerValue): FindLabel {
  if (!selection) return null;
  if (selection === OTHER) return otherText.trim() ? { text: otherText.trim() } : null;
  return { alias_id: selection };
}

/** One community version card: label, Match %, Unconfirmed, counts and the apply action. */
function VersionCard({ result, busy, canApply, onApply }: {
  result: FindResult; busy: boolean; canApply: boolean; onApply: () => void;
}) {
  const heading = useId();
  return <li className="master-version-card" aria-labelledby={heading}>
    <div className="master-version-card-title">
      <h4 id={heading}>{result.label}</h4>
      <MatchPercent value={result.matchPercent} />
      {result.unconfirmed && <UnconfirmedLabel />}
    </div>
    <p className="muted-copy">{result.filterCount} {result.filterCount === 1 ? "filter" : "filters"} · {result.contributorCount} {result.contributorCount === 1 ? "contributor" : "contributors"}</p>
    {result.alignmentRequired && <p className="muted-copy">Needs alignment with your file before it applies.</p>}
    {canApply && (result.linkedLinkId
      ? <p className="community-strength">In use for this file</p>
      : <button type="button" className="btn-primary compact-button" disabled={busy} onClick={onApply}
        aria-label={`Use ${result.label} filters`}>Use these filters</button>)}
  </li>;
}

/**
 * Find community master sets for a local version and show them as version cards. Applying a different
 * version while another is linked asks for confirmation and then replaces the link.
 */
export function MasterFindResults({ target }: { target: MasterTarget }) {
  const maySearch = useCan("community.search");
  const mayApply = useCan("community.download");
  const [label, setLabel] = useState<PickerValue>({ selection: "theatrical", otherText: "" });
  const [response, setResponse] = useState<FindResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const [replacing, setReplacing] = useState<FindResult | null>(null);
  if (!maySearch) return null;

  const find = async (refresh: boolean) => {
    setBusy(true); setMessage(null); setReplacing(null);
    try { setResponse(await findCommunityVersions(target, findLabel(label), refresh)); }
    catch (reason) { setMessage({ text: masterErrorText(reason), error: true }); }
    finally { setBusy(false); }
  };
  const current = response?.results.find((result) => result.linkedLinkId);
  const apply = async (result: FindResult, replace: boolean) => {
    if (!response) return;
    if (current && current !== result && !replace) { setReplacing(result); return; }
    setBusy(true); setMessage(null); setReplacing(null);
    try {
      const { link } = await applyCommunityMaster({ target, findKey: response.findKey,
        communityVersionId: result.communityVersionId, replace });
      setResponse({ ...response, results: response.results.map((item) => ({ ...item,
        linkedLinkId: item.communityVersionId === result.communityVersionId ? link.linkId : null })) });
      setMessage({ text: link.alignmentState === "required"
        ? `${result.label} filters added. Align them on the item page before they apply.`
        : `${result.label} filters added to this item.`, error: false });
      notifyCommunityMastersChanged();
    } catch (reason) { setMessage({ text: masterErrorText(reason), error: true }); }
    finally { setBusy(false); }
  };

  return <section className="master-find" aria-label="Community master filter sets">
    <header className="community-results-header"><h3>Community versions</h3>
      <div className="button-row">
        <button type="button" className="btn-secondary" disabled={busy} onClick={() => void find(response !== null)}>
          {busy ? "Finding…" : response ? "Refresh community versions" : "Find community versions"}</button>
      </div></header>
    <VersionPicker value={label} onChange={setLabel} disabled={busy} label="Your version" />
    {message && <p role={message.error ? "alert" : "status"} className={message.error ? "form-error" : "form-status"}>{message.text}</p>}
    {response?.error && <p role="alert" className="form-error">{masterErrorText(response.error)}</p>}
    {replacing && <div className="community-notice" role="group" aria-label="Replace community filters">
      <p>Replace {current?.label ?? "the current version"} with {replacing.label}? Filters from the current version are kept as history until you confirm.</p>
      <div className="button-row"><button type="button" className="btn-secondary" onClick={() => setReplacing(null)}>Cancel</button>
        <button type="button" className="btn-primary" disabled={busy} onClick={() => void apply(replacing, true)}>Replace</button></div>
    </div>}
    {response && !response.error && (response.results.length
      ? <ul className="master-version-cards">{response.results.map((result) => <VersionCard key={result.communityVersionId}
        result={result} busy={busy} canApply={mayApply} onApply={() => void apply(result, false)} />)}</ul>
      : <p role="status">No community versions match this file.</p>)}
  </section>;
}
