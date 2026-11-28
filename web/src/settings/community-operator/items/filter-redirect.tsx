import { useEffect } from "react";
import { readOperatorFilterContext } from "../../../api/api-community-operator-catalog";
import { DataState, useOperatorData } from "../shared/operator-data";
import { itemLink, operatorLink } from "../routes";

/** Resolve `#/filters/<id>` to its item page with the filter selected, replacing the history entry. */
export function FilterRedirect({ filterId }: { filterId: string }) {
  const { data, status, refresh } = useOperatorData(`filter-redirect:${filterId}`, signal => readOperatorFilterContext(filterId, signal), !!filterId);
  useEffect(() => {
    if (data?.item) window.location.replace(itemLink(data.item.workId, data.versionLabel?.versionId, data.filter.filterId));
  }, [data]);
  return <section aria-label="Opening filter"><DataState status={filterId ? status : "missing"} onRetry={refresh} />
    {(status === "missing" || !filterId || (data && !data.item)) && <p><a href={operatorLink("inspect", { kind: "filter", selected: filterId })}>Inspect the filter by ID</a></p>}
  </section>;
}
