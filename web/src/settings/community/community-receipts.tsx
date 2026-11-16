import { useState } from "react";
import { recoverCommunityReceipts, retryCommunityReceipt } from "../../api/api-community-receipts";
import { communityError, failureMessage } from "./community-state";
import { useCommunityLifetime } from "./community-access";

export function CommunityReceipts({ userId }: { userId?: number }) {
  const [imports, setImports] = useState<string[]>([]);
  const [outcomes, setOutcomes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState(false);
  const lifetime = useCommunityLifetime();
  async function run(importId?: string) {
    if (busy || !lifetime.current()) return;
    setBusy(true); setError("");
    const options = { userId, signal: lifetime.signal() };
    try {
      if (importId) {
        await retryCommunityReceipt(importId, options);
        if (lifetime.current()) setOutcomes(previous => ({ ...previous, [importId]: "Acknowledged" }));
      } else {
        const result = await recoverCommunityReceipts(options);
        if (lifetime.current()) { setImports(result); setOutcomes({}); setChecked(true); }
      }
    } catch (reason) {
      if (!lifetime.current()) return;
      const failure = communityError(reason);
      if ([403, 404].includes(failure.status)) { setImports([]); setOutcomes({}); setChecked(false); lifetime.refresh(); }
      else if (importId) setOutcomes(previous => ({ ...previous, [importId]: "Not acknowledged. Retry retained." }));
      setError(failureMessage(failure.code));
    } finally { if (lifetime.current()) setBusy(false); }
  }
  return <section className="community-settings" aria-labelledby="community-receipts-heading" aria-busy={busy}>
    <h2 id="community-receipts-heading">Import receipts</h2>
    <button className="btn-secondary" disabled={busy} onClick={() => void run()}>Recover import receipts</button>
    {error && <p role="alert">{error}</p>}
    {checked && !imports.length && <p role="status">No eligible import receipts.</p>}
    {imports.length > 0 && <ul>{imports.map(importId => <li key={importId}>
      <code>{importId}</code> <span role="status">{outcomes[importId] ?? "Recovered locally. Not sent."}</span>{" "}
      <button className="btn-secondary" disabled={busy || outcomes[importId] === "Acknowledged"} onClick={() => void run(importId)}>Retry receipt {importId}</button>
    </li>)}</ul>}
  </section>;
}