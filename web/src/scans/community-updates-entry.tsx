import { useId, useState } from "react";
import type { JellyfinLibrary } from "../api/api-media-types";
import type { SyncNotification } from "../api/api-community-master-types";
import { resolveLegacyItem } from "../api/api-stable-media";
import { editarrItemRoute } from "../routes";
import type { useCommunitySync } from "./use-community-sync";

const kindText: Record<SyncNotification["kind"], string> = {
  changed: "filters updated", merged: "version merged", unavailable: "version unavailable",
};

/** Find the item page for a notification. Notifications carry no library, so each library is tried in turn. */
async function itemRoute(itemId: string, libraries: JellyfinLibrary[]) {
  for (const library of libraries) {
    try {
      const item = await resolveLegacyItem(itemId, library.id);
      return editarrItemRoute(item.title_id, item.selected_version_id || undefined);
    } catch { /* Try the next library. */ }
  }
  return null;
}

/**
 * Sidebar notification entry for the daily community sync: an unread count and a list of updated
 * items as links to their item pages, where the update banner offers Review.
 */
export function CommunityUpdatesEntry({ sync, libraries, onNavigate }: {
  sync: ReturnType<typeof useCommunitySync>; libraries: JellyfinLibrary[]; onNavigate: (route: string) => boolean;
}) {
  const listId = useId();
  const [expanded, setExpanded] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const notifications = sync.status?.notifications ?? [];
  if (!notifications.length) return null;
  const unread = sync.status?.unreadCount ?? 0;

  const open = async (notification: SyncNotification) => {
    setOpening(notification.notificationId);
    setError(null);
    const route = await itemRoute(notification.itemId, libraries);
    setOpening(null);
    if (!route) { setError(`${notification.title} is no longer available.`); return; }
    if (!notification.read) void sync.markRead([notification.notificationId]);
    onNavigate(route);
  };

  return <section className="sidebar-section community-updates-entry">
    <button type="button" className="sidebar-disclosure" aria-expanded={expanded} aria-controls={listId}
      onClick={() => setExpanded(!expanded)}>
      <span>Filter updates{unread > 0 && <span className="community-updates-count" aria-label={`${unread} unread`}>{unread}</span>}</span>
      <span aria-hidden="true">{expanded ? "⌄" : "›"}</span>
    </button>
    {expanded && <div id={listId} className="sidebar-children">
      <ul className="community-updates-list">{notifications.map((notification) => <li key={notification.notificationId}>
        <button type="button" className={`sidebar-link nested${notification.read ? "" : " community-update-unread"}`}
          disabled={opening !== null} onClick={() => void open(notification)}>
          <span>{notification.title}</span><small>{kindText[notification.kind]}</small>
        </button>
      </li>)}</ul>
      {error && <p className="sidebar-hint" role="alert">{error}</p>}
      {unread > 0 && <button type="button" className="sidebar-link nested" onClick={() => void sync.markRead("all")}>Mark all as read</button>}
    </div>}
  </section>;
}
