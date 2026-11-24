import { useState } from "react";
import { operatorResources, type OperatorResource } from "../../api/api-community-operator";
import { attestationIdentifier, identifier } from "../../api/api-community-operator-checks";
import { resourceLink, type OperatorRoute } from "./routes";

const hints: Partial<Record<OperatorResource, string>> = { attestation: "Submission ID:entry ID", vote: "Filter ID" };

/** Look up any moderation resource by ID; votes also need the voting contributor. */
export function ResourceLookup({ route }: { route: OperatorRoute }) {
  const initial = operatorResources.includes(route.kind as OperatorResource) ? route.kind as OperatorResource : "filter";
  const [kind, setKind] = useState<OperatorResource>(initial);
  const [id, setId] = useState(route.selected ?? "");
  const [contributor, setContributor] = useState(route.contributor ?? "");
  const valid = (kind === "attestation" ? attestationIdentifier : identifier)(id) && (kind !== "vote" || identifier(contributor));
  return <section aria-label="Resource lookup"><h3>Look up a resource</h3>
    <form onSubmit={event => {
      event.preventDefault();
      if (valid) window.location.hash = resourceLink(kind, id, kind === "vote" ? contributor : undefined);
    }}>
      <label htmlFor="operator-lookup-kind">Resource type</label>
      <select id="operator-lookup-kind" value={kind} onChange={event => setKind(event.target.value as OperatorResource)}>
        {operatorResources.map(value => <option key={value} value={value}>{value.replace("_", " ")}</option>)}</select>
      <label htmlFor="operator-lookup-id">{hints[kind] ?? "ID"}</label>
      <input id="operator-lookup-id" value={id} maxLength={161} onChange={event => setId(event.target.value)} />
      {kind === "vote" && <><label htmlFor="operator-lookup-contributor">Voting contributor ID</label>
        <input id="operator-lookup-contributor" value={contributor} maxLength={128} onChange={event => setContributor(event.target.value)} /></>}
      <button disabled={!valid}>Inspect resource</button>
    </form></section>;
}
