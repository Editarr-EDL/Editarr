import { readOperatorInspection, readOperatorPage, readOperatorUsage, operatorResources,
  type OperatorResource } from "../../../api/api-community-operator";
import { DataState, useOperator, useOperatorData } from "../shared/operator-data";
import { PageHead } from "../shared/ui";
import { emptyDetail, InspectionPanel, type InspectionDetail } from "./inspection-panel";
import { ResourceLookup } from "./resource-lookup";

/** Inspect a resource with its per-kind detail pages (contributor usage and keys, version filters and submissions). */
export async function loadDetail(params: Record<string, string | null>, signal: AbortSignal) {
  const kind = params.kind as OperatorResource, id = params.selected!;
  const detail: InspectionDetail = { ...emptyDetail };
  detail.inspection = await readOperatorInspection(kind, id, signal, kind === "vote" ? params.contributor ?? "" : undefined);
  if (kind === "contributor") {
    detail.usage = await readOperatorUsage(id, signal);
    detail.keys = await readOperatorPage("keys", params.keysAfter, signal, id);
  }
  if (kind === "version") {
    detail.filters = await readOperatorPage("filters", params.filtersAfter, signal, id);
    detail.submissions = await readOperatorPage("submissions", params.submissionsAfter, signal, id);
  }
  return detail;
}

/** Generic resource lookup and inspection; every moderation command stays reachable here by ID. */
export function InspectPage({ params }: { params: Record<string, string | null> }) {
  const { onAccessFailure, refreshCounts } = useOperator();
  const valid = operatorResources.includes(params.kind as OperatorResource) && !!params.selected;
  const key = JSON.stringify(params);
  const { data, status, refresh } = useOperatorData(key, signal => loadDetail(params, signal), valid);
  return <section aria-labelledby="page-title">
    <PageHead eyebrow="System" title="Resource lookup"
      intro="Inspect any moderation resource by ID and apply the actions its current state permits." />
    <ResourceLookup key={key} route={params} />
    {valid && <><DataState status={status} onRetry={refresh} />
      {data && <InspectionPanel params={params} detail={data} fresh={status === "fresh"} onAccessFailure={onAccessFailure}
        onApplied={() => { refresh(); refreshCounts(); }} />}</>}
  </section>;
}
