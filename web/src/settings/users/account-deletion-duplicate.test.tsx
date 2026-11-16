import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOptionalAuthenticationGate, usePrincipal } from "../../auth/auth-gate";
import type { Principal } from "../../auth/session";
import { deletionPreview, deletionResult, summary } from "./account-deletion.test-support";
import { AccountLinkSection } from "./account-link-section";
import { preview } from "./account-link.test-support";
import { ordinaryAccount, ownerPrincipal, unlinkedAccount } from "./users.test-support";

vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn(), useOptionalAuthenticationGate: vi.fn() }));

const PASSWORD = "owner synthetic password";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const holder = (role: "user" | "admin") => ({ ...ordinaryAccount, id: 7, username: "casey", display_name: "Casey", source: "jellyfin" as const, role });

const ACTIVE = { current: "active", after: "active", cause: null } as const;

/**
 * Answer each route of the link and deletion flows; the holder of `jf-casey` is account 7.
 * The link target, account 3, is disabled without a link unless `target` is `ACTIVE`.
 */
function server(role: "user" | "admin" = "user", connection: "answers" | "drops" = "answers", target?: typeof ACTIVE) {
  const requests: { method: string; path: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    requests.push({ method, path: url.pathname + url.search, body });
    const proofs = requests.filter(request => request.path.endsWith("/link/proof")).length;
    if (url.pathname.endsWith("/link/proof")) return json({ proof: `proof-${proofs}`, expires_at: "2026-10-27T12:10:00Z", confirmation: "a".repeat(64), preview: preview({ blockers: proofs === 1 ? ["jellyfin_identity_in_use"] : [], ...(target ? { status: target } : {}) }) });
    if (url.pathname.endsWith("/jellyfin/sync")) return json({ running: false, last_run: null, accounts: [{ user_id: 7, jellyfin_user_id: "jf-casey", name: "Casey", link_state: "verified", upstream_state: null, last_verified_at: null, grace_expires_at: null, last_attempt_at: null, last_outcome: null, last_failure: null }] });
    if (url.pathname.endsWith("/users/7")) return json(holder(role));
    if (url.pathname.endsWith("/users")) return json({ total: 2, offset: 0, limit: 20, items: [summary(3, "Pat", target ? {} : { status: "disabled", disable_cause: "unlinked" }), summary(7, "Casey")] });
    if (url.pathname.endsWith("/users/7/deletion") && method === "GET") return json(deletionPreview({ user_id: 7, mode: "transfer", destination_id: 3, transfer: { files_moved: 1, policy_files: 0 } }));
    if (url.pathname.endsWith("/users/7/deletion") && connection === "drops") throw new TypeError("Failed to fetch");
    if (url.pathname.endsWith("/users/7/deletion")) return json(deletionResult({ user_id: 7, mode: "transfer", destination_id: 3, reviews_retained: [], reviews_transferred: ["review-1"], files_moved: 1, identity_released: true }));
    throw new Error(`Unexpected request ${method} ${url.pathname}`);
  }));
  return requests;
}

function proveLink(dialog: HTMLElement) {
  fireEvent.change(within(dialog).getByLabelText("Jellyfin username"), { target: { value: "Casey" } });
  fireEvent.change(within(dialog).getByLabelText("Jellyfin password"), { target: { value: "jellyfin synthetic password" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
  const fresh = screen.getByRole("dialog", { name: "Confirm your Jellyfin password" });
  fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: PASSWORD } });
  fireEvent.click(within(fresh).getByRole("button", { name: "Prove identities" }));
}

function openLink(principal: Principal, onChanged = vi.fn()) {
  vi.mocked(usePrincipal).mockReturnValue(principal);
  render(<AccountLinkSection account={unlinkedAccount} principal={principal} own={false} onChanged={onChanged} />);
  fireEvent.click(screen.getByRole("button", { name: "Link to Jellyfin" }));
  return screen.getByRole("dialog", { name: "Link to Jellyfin" });
}

describe("Duplicate identity resolution", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(useOptionalAuthenticationGate).mockReturnValue(null); });

  it("follows the blocker into a reviewed transfer to an active link target, then restarts linking without merging", async () => {
    const requests = server("user", "answers", ACTIVE);
    const onChanged = vi.fn();
    const dialog = openLink(ownerPrincipal, onChanged);
    proveLink(dialog);
    const action = await within(dialog).findByRole("button", { name: "Review deleting or transferring Casey" });
    expect(within(dialog).queryByRole("button", { name: "Confirm link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /merge/i })).not.toBeInTheDocument();
    fireEvent.click(action);
    const deletion = screen.getByRole("dialog", { name: "Delete Casey" });
    fireEvent.click(within(deletion).getByRole("radio", { name: /Transfer data/ }));
    await waitFor(() => expect(within(deletion).getByLabelText("Destination account")).toHaveValue("3"));
    fireEvent.click(within(deletion).getByRole("button", { name: "Review impact" }));
    expect(await within(deletion).findByRole("region", { name: "Deletion impact" })).toHaveTextContent("move to Pat. Pat becomes the owner; Casey stays recorded as the original author.");
    fireEvent.click(within(deletion).getByRole("button", { name: "Delete account" }));
    const fresh = screen.getByRole("dialog", { name: "Confirm deleting Casey" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: PASSWORD } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Delete account" }));
    expect(await within(deletion).findByRole("region", { name: "Deletion result" })).toHaveTextContent("1 review transferred to Pat. 1 personal export file moved.");
    fireEvent.click(within(deletion).getByRole("button", { name: "Done" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Casey was deleted. Prove the Jellyfin identity again to review a fresh link preview.");
    expect(onChanged).toHaveBeenCalled();
    proveLink(dialog);
    expect(await within(dialog).findByRole("button", { name: "Confirm link" })).toBeVisible();
    expect(requests.filter(request => request.method === "POST").map(request => request.path)).toEqual(["/api/users/3/link/proof", "/api/users/7/deletion", "/api/users/3/link/proof"]);
    expect(requests.find(request => request.method === "POST" && request.path === "/api/users/7/deletion")!.body).toMatchObject({ mode: "transfer", destination_id: 3 });
  });

  it("never offers a disabled link target as the destination for the holder's data", async () => {
    server();
    const dialog = openLink(ownerPrincipal);
    proveLink(dialog);
    expect(await within(dialog).findByText("Casey holds this Jellyfin identity. Review deleting it, then prove the identity again. Pat can't receive its data until it is linked.")).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Review deleting Casey" }));
    const deletion = screen.getByRole("dialog", { name: "Delete Casey" });
    expect(within(deletion).getByRole("radio", { name: /Keep data/ })).toBeVisible();
    fireEvent.click(within(deletion).getByRole("radio", { name: /Transfer data/ }));
    const destination = await within(deletion).findByLabelText("Destination account");
    expect(destination).toHaveValue("");
    expect(within(destination).queryByRole("option", { name: "Pat" })).not.toBeInTheDocument();
  });

  it("restarts linking without claiming a deletion when the deletion's outcome is unknown", async () => {
    server("user", "drops");
    const onChanged = vi.fn();
    const dialog = openLink(ownerPrincipal, onChanged);
    proveLink(dialog);
    fireEvent.click(await within(dialog).findByRole("button", { name: "Review deleting Casey" }));
    const deletion = screen.getByRole("dialog", { name: "Delete Casey" });
    fireEvent.click(within(deletion).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(deletion).getByRole("button", { name: "Review impact" }));
    fireEvent.click(await within(deletion).findByRole("button", { name: "Delete account" }));
    const fresh = screen.getByRole("dialog", { name: "Confirm deleting Casey" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: PASSWORD } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Delete account" }));
    const stopped = await within(deletion).findByRole("region", { name: "Deletion stopped" });
    fireEvent.click(within(stopped).getByRole("button", { name: "Close" }));
    const notice = await within(dialog).findByRole("alert");
    expect(notice.textContent).toBe("Editarr did not confirm whether Casey was deleted. Prove the Jellyfin identity again to see whether it is still in use.");
    expect(onChanged).toHaveBeenCalled();
  });

  it("names a protected holder without offering deletion to an admin", async () => {
    const requests = server("admin");
    const admin: Principal = { ...ownerPrincipal, userId: 2 as Principal["userId"], role: "admin", capabilities: new Set([...ownerPrincipal.capabilities].filter(capability => capability !== "accounts.admins.manage")) };
    const dialog = openLink(admin);
    proveLink(dialog);
    expect(await within(dialog).findByText("Casey holds this Jellyfin identity. You can't delete that account; only the owner can delete an admin.")).toBeVisible();
    expect(within(dialog).queryByRole("button", { name: /Review deleting/ })).not.toBeInTheDocument();
    expect(requests.some(request => request.path.includes("/deletion"))).toBe(false);
  });

  it("offers no action without the linked-account list", async () => {
    const requests = server();
    const dialog = openLink({ ...ownerPrincipal, capabilities: new Set([...ownerPrincipal.capabilities].filter(capability => capability !== "accounts.jellyfin.sync")) });
    proveLink(dialog);
    expect(await within(dialog).findByRole("region", { name: "Link blockers" })).toHaveTextContent("Delete one of the accounts or transfer its data explicitly, then link again.");
    expect(within(dialog).queryByRole("button", { name: /Review deleting/ })).not.toBeInTheDocument();
    expect(requests.some(request => request.path.includes("/jellyfin/sync"))).toBe(false);
  });
});
