import type { OperatorRoute } from "../routes";
import { HomePage } from "../home/home-page";
import { ItemsPage } from "../items/items-page";
import { ItemPage } from "../items/item-page";
import { FilterRedirect } from "../items/filter-redirect";
import { ActivityPage } from "../activity/activity-page";
import { AuditPage } from "../activity/audit-page";
import { ContributorsPage } from "../contributors/contributors-page";
import { QueuePage } from "../queue/queue-page";
import { RecordsPage } from "../records/records-page";
import { BackupsPage } from "../backups/backups-page";
import { HealthPage } from "../health/health-page";
import { InspectPage } from "../inspect/inspect-page";

/** Render the page for `route`; keys reset page state when the addressed resource changes. */
export function OperatorPageView({ route }: { route: OperatorRoute }) {
  const { page, path, params } = route;
  switch (page) {
    case "items": return path[0] ? <ItemPage key={path[0]} workId={path[0]} params={params} /> : <ItemsPage params={params} />;
    case "filters": return <FilterRedirect key={path[0]} filterId={path[0] ?? ""} />;
    case "activity": return <ActivityPage params={params} />;
    case "contributors": return <ContributorsPage params={params} />;
    case "versions": case "aliases": case "reports": return <RecordsPage key={page} view={page} params={params} />;
    case "queue": return <QueuePage kind={path[0] ?? "all"} params={params} />;
    case "backups": return <BackupsPage />;
    case "health": return <HealthPage params={params} />;
    case "audit": return <AuditPage params={params} />;
    case "inspect": return <InspectPage params={params} />;
    default: return <HomePage params={params} />;
  }
}
