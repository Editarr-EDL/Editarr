import { readOperatorPage } from "../../../api/api-community-operator";
import { contributorColumns, RecordTable } from "../records/record-tables";
import { DataState, useOperatorData } from "../shared/operator-data";
import { operatorLink } from "../routes";

/** Every contributor, including idle and suspended accounts, in join order with keyset paging. */
export function AllContributors({ params }: { params: Record<string, string | null> }) {
  const after = params.after ?? null;
  const { data, status, refresh } = useOperatorData(`contributors:all:${after}`, signal => readOperatorPage("contributors", after, signal));
  return <div>
    <DataState status={status} onRetry={refresh} />
    {data && <RecordTable label="All contributors" rows={data.items} columns={contributorColumns} idOf={row => String(row.contributorId)}
      selected={params.selected} details={row => operatorLink("contributors", { tab: "all", after, selected: String(row.contributorId) })} />}
    <p className="foot">Includes contributors with no activity in any period and suspended accounts.
      {after && <> <a href={operatorLink("contributors", { tab: "all" })}>First page</a></>}
      {data?.nextCursor && <> <a href={operatorLink("contributors", { tab: "all", after: data.nextCursor })}>Next page</a></>}</p>
  </div>;
}
