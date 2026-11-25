import { useId, useState } from "react";
import type { SyncNotification } from "../api/api-community-master-types";
import { libraryItemRoute } from "../routes";
import type { useCommunitySync } from "./use-community-sync";

const kindText: Record<SyncNotification["kind"], string> = {
  changed: "filters updated", merged: "version merged", unavailable: "version unavailable",
};

/**
 * Sidebar notification entry for the daily community sync: an unread count, any sync problem, and a
 * list of updated items as links to the notified version's item page, where the banner offers Review.
 */
export function CommunityUpdatesEntry({ sync, onNavigate }: {
  sync: ReturnType<typeof useCommunitySync>; onNavigate: (route: string) => boolean;
}) {
  const listId = useId();
  const [expanded, setExpanded] = useState(false);
  const notifications = sync.status?.notifications ?? [];
  const problem = sync.error ?? (sync.status?.state === "failed" ? "The last filter update check failed." : null);
  if (!notifications.length && !problem) return null;
  const unread = sync.status?.unreadCount ?? 0;

  const open = (notification: SyncNotification) => {
    if (!onNavigate(libraryItemRoute(notification.libraryId, notification.itemId, notification.mediaSourceId, "community"))) return;
    if (!notification.read) void sync.markRead([notification.notificationId]);
  };

  return <section className="sidebar-section community-updates-entry">
    <button type="button" className="sidebar-disclosure" aria-expanded={expanded} aria-controls={listId}
      onClick={() => setExpanded(!expanded)}>
      <span>Filter updates{unread > 0 && <span className="community-updates-count" aria-label={`${unread} unread`}>{unread}</span>}</span>
      <span aria-hidden="true">{expanded ? "⌄" : "›"}</span>
    </button>
    {problem && <p className="sidebar-hint" role="alert">{problem}</p>}
    {expanded && <div id={listId} className="sidebar-children">
      <ul className="community-updates-list">{notifications.map((notification) => <li key={notification.notificationId}>
        <a className={`sidebar-link nested${notification.read ? "" : " community-update-unread"}`}
          href={`#/${libraryItemRoute(notification.libraryId, notification.itemId, notification.mediaSourceId, "community")}`}
          onClick={(event) => { event.preventDefault(); open(notification); }}>
          <span>{notification.title}</span><small>{kindText[notification.kind]}</small>
        </a>
      </li>)}</ul>
      {unread > 0 && <button type="button" className="sidebar-link nested" onClick={() => void sync.markRead("all")}>Mark all as read</button>}
    </div>}
  </section>;
}
