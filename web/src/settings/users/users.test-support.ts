import type { AccountDetail } from "../../api/api-users";
import { CAPABILITIES } from "../../auth/capabilities";
import type { Principal } from "../../auth/session";

export const ownerPrincipal: Principal = {
  userId: 1 as Principal["userId"], revision: 1 as Principal["revision"], role: "owner",
  linkState: "verified", displayName: "Owner",
  capabilities: new Set(CAPABILITIES),
};
export const ordinaryAccount: AccountDetail = {
  id: 3, username: null, display_name: "Pat", source: "jellyfin", role: "user", status: "active", disable_cause: null,
  avatar: null, last_login_at: null, created_at: "2026-10-24T12:00:00Z",
  overrides: { permissions: false, media_access: true, filter_policy: true },
  email: null, timezone: null, language: "en",
  read_only_fields: ["username", "display_name", "avatar"], available_languages: ["en"], link_cleanup: null, successor_refusal: null,
};
/** An account the JF-06 migration disabled because it has no Jellyfin link. */
export const unlinkedAccount: AccountDetail = {
  ...ordinaryAccount, username: null, source: "local", status: "disabled", disable_cause: "unlinked", read_only_fields: [],
  successor_refusal: "successor_disabled",
};
