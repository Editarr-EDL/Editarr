import type { AccountSummary } from "../../api/api-users";
import type { Capability } from "../../auth/capabilities";
import { can, type Principal } from "../../auth/session";

export function mayManageAccount(principal: Principal, account: AccountSummary, capability: Capability): boolean {
  if (account.id === principal.userId || account.role === "owner") return false;
  if (account.role === "admin" && (principal.role !== "owner" || !can(principal, "accounts.admins.manage"))) return false;
  return can(principal, capability);
}

/** The status cell: an account disabled for lacking a Jellyfin link says so. */
export function accountStatusLabel(account: Pick<AccountSummary, "status" | "disable_cause">): string {
  return account.disable_cause === "unlinked" ? "Disabled: no Jellyfin link" : account.status;
}

/** Only linking enables an account without a Jellyfin link; the server refuses a plain enable. */
export function mayEnable(principal: Principal, account: AccountSummary): boolean {
  return account.status === "disabled" && account.source === "jellyfin" && mayManageAccount(principal, account, "accounts.enable.other");
}

/** Whether the directory offers **Link to Jellyfin** for another account. */
export function mayLinkOther(principal: Principal, account: AccountSummary): boolean {
  return account.source === "local" && mayManageAccount(principal, account, "accounts.link.other");
}

export function accountDate(value: string | null): string {
  if (!value) return "Never";
  return new Date(value).toLocaleString();
}