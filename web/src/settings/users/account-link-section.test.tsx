import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountDetail } from "../../api/api-users";
import { useOptionalAuthenticationGate, usePrincipal } from "../../auth/auth-gate";
import { OperationReceipts } from "../../auth/operation-receipts";
import type { Principal } from "../../auth/session";
import { AccountLinkSection } from "./account-link-section";
import { linkServer, preview } from "./account-link.test-support";
import { ordinaryAccount, ownerPrincipal, unlinkedAccount } from "./users.test-support";

vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn(), useOptionalAuthenticationGate: vi.fn() }));

const linked: AccountDetail = { ...ordinaryAccount, read_only_fields: ["display_name", "avatar"] };
const admin: Principal = { ...ownerPrincipal, userId: 2 as Principal["userId"], role: "admin", capabilities: new Set([...ownerPrincipal.capabilities].filter(capability => capability !== "accounts.admins.manage")) };
const member: Principal = { ...ownerPrincipal, userId: 3 as Principal["userId"], role: "user", capabilities: new Set(["profile.view.own", "profile.edit.own"]) };
const syncStatus = (state: string, grace: string | null = null) => ({
  running: false, last_run: null,
  accounts: [{ user_id: 3, jellyfin_user_id: "jf-casey", name: "Casey", link_state: state, upstream_state: "active", last_verified_at: null, grace_expires_at: grace, last_attempt_at: null, last_outcome: null, last_failure: null }],
});

function section(account: AccountDetail, principal: Principal, own = false) {
  return render(<AccountLinkSection account={account} principal={principal} own={own} onChanged={vi.fn()} />);
}

describe("Account link section", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
    vi.mocked(useOptionalAuthenticationGate).mockReturnValue(null);
  });

  it("relinks a replaced-server account on an explicit admin attestation without asking for its password", async () => {
    const server = linkServer();
    server.answer("sync", 200, syncStatus("server_replaced"));
    server.proven({ preview: preview({ kind: "relink", proof_basis: "admin_attested", identity: { current: { server_id: "old", user_id: "jf-casey" }, after: { server_id: "new", user_id: "jf-casey-2" } }, export_id: { current: "jf-casey", after: "jf-casey-2" }, media: { current: "suspended", after: "jellyfin", all_libraries: true, libraries: 0, parental_limit: false } }) });
    section(linked, ownerPrincipal);
    expect(await screen.findByText(/Media access is suspended until an admin relinks the account/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Relink to current server" }));
    const dialog = screen.getByRole("dialog", { name: "Relink to the current Jellyfin server" });
    expect(within(dialog).queryByLabelText(/own Editarr password/)).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent("recorded as an admin attestation");
    fireEvent.change(within(dialog).getByLabelText("Jellyfin username"), { target: { value: "Casey" } });
    fireEvent.change(within(dialog).getByLabelText("Jellyfin password"), { target: { value: "replacement synthetic" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const fresh = screen.getByRole("dialog", { name: "Confirm your Jellyfin password" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic" } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Prove identities" }));
    const impact = await within(dialog).findByRole("region", { name: "Link impact" });
    expect(impact).toHaveTextContent("Admin attested. You confirm that this Jellyfin user is the same person");
    expect(impact).toHaveTextContent("From old to new");
    expect(impact).toHaveTextContent("Suspended until relinked becomes all Jellyfin libraries.");
    expect(server.bodies.at(-1)?.body).not.toHaveProperty("account_password");
  });

  it.each([
    ["outage_grace", "2026-10-28T12:00:00Z", /Jellyfin cannot be reached. Media access continues from the last successful check until .+ Profile access is unaffected./],
    ["outage_expired", null, /Only profile access remains; media is blocked until Jellyfin answers again./],
  ])("explains %s without offering a link", async (state, grace, text) => {
    linkServer().answer("sync", 200, syncStatus(state, grace));
    section(linked, ownerPrincipal);
    expect(await screen.findByText(text)).toBeVisible();
    expect(screen.queryByRole("button", { name: /link/i })).not.toBeInTheDocument();
  });

  it("offers no unlink for a verified linked account", async () => {
    linkServer().answer("sync", 200, syncStatus("verified"));
    section(linked, ownerPrincipal);
    expect(await screen.findByText(/A linked account cannot be unlinked/)).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("hides the action from an admin for another admin and from a person without the link capability", () => {
    section({ ...unlinkedAccount, id: 5, role: "admin" }, admin);
    expect(screen.queryByRole("button", { name: "Link to Jellyfin" })).not.toBeInTheDocument();
    cleanup();
    section(unlinkedAccount, admin);
    expect(screen.getByRole("button", { name: "Link to Jellyfin" })).toBeVisible();
    cleanup();
    section(unlinkedAccount, member, true);
    expect(screen.getByText("Not linked to Jellyfin. The account cannot sign in until it is linked.")).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("offers the owner a self relink of its replaced-server link without promising local sign-in", () => {
    const owner = { ...ownerPrincipal, linkState: "server_replaced" as const, capabilities: new Set([...ownerPrincipal.capabilities].filter(capability => capability !== "accounts.jellyfin.sync")) };
    section({ ...linked, id: 1, role: "owner" }, owner, true);
    expect(screen.getByRole("status")).toHaveTextContent("Media access is suspended until an admin relinks the account to the current server.");
    expect(screen.getByRole("status")).not.toHaveTextContent("local sign-in");
    fireEvent.click(screen.getByRole("button", { name: "Relink to current server" }));
    expect(screen.queryByLabelText(/own Editarr password/)).not.toBeInTheDocument();
  });

  it("keeps the link result after the signed-in view remounts", async () => {
    const server = linkServer();
    server.proven({ preview: preview({ proof_basis: "actor_authentication" }) });
    server.answer("link", 200, { state: "removing", preview: preview() });
    const own = { ...unlinkedAccount, id: 1, role: "owner" as const };
    const view = (key: number) => <OperationReceipts><AccountLinkSection key={key} account={own} principal={ownerPrincipal} own onChanged={vi.fn()} /></OperationReceipts>;
    const { rerender } = render(view(1));
    fireEvent.click(screen.getByRole("button", { name: "Link to Jellyfin" }));
    const dialog = screen.getByRole("dialog", { name: "Link to Jellyfin" });
    fireEvent.change(within(dialog).getByLabelText("Jellyfin username"), { target: { value: "Drew" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    let fresh = screen.getByRole("dialog", { name: "Confirm your Jellyfin password" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic" } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Prove identities" }));
    fireEvent.click(await within(dialog).findByRole("button", { name: "Confirm link" }));
    fresh = screen.getByRole("dialog", { name: "Confirm link" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic" } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Link account" }));
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Link result" })).toBeVisible());
    rerender(view(2));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Linked to Jellyfin user Casey. 1 personal export file now uses export ID jf-casey.")).toBeVisible();
    expect(screen.queryByText(/not removed yet/)).not.toBeInTheDocument();
  });

  it("shows pending old-file removal from the account read until the read says it finished", () => {
    const view = (link_cleanup: AccountDetail["link_cleanup"]) => <AccountLinkSection account={{ ...linked, link_cleanup }} principal={member} own onChanged={vi.fn()} />;
    const { rerender } = render(view({ state: "removing", pending: 2, removed: 0, absent: 0, kept: 0 }));
    expect(screen.getByRole("status")).toHaveTextContent("Some old export files are not removed yet. 2 are waiting; Editarr retries their removal at startup and every hour.");
    rerender(view({ state: "completed", pending: 0, removed: 2, absent: 0, kept: 0 }));
    expect(screen.queryByText(/not removed yet|were removed/)).not.toBeInTheDocument();
  });

  it("says which old files were left in place instead of claiming all were removed", () => {
    section({ ...linked, link_cleanup: { state: "completed", pending: 0, removed: 1, absent: 0, kept: 1 } }, member, true);
    expect(screen.getByRole("status")).toHaveTextContent("1 old export file was removed. 1 old export file was left in place because it holds content Editarr did not write.");
  });

  describe("after a link with nothing to remove", () => {
    const own: AccountDetail = { ...unlinkedAccount, id: 1, role: "owner" };

    async function linkWith(exports: ReturnType<typeof preview>["exports"], linkCleanup: AccountDetail["link_cleanup"]) {
      const server = linkServer();
      server.proven({ preview: preview({ proof_basis: "actor_authentication", exports }) });
      server.answer("link", 200, { state: "completed", preview: preview({ exports }) });
      const view = (account: AccountDetail) => <OperationReceipts><AccountLinkSection account={account} principal={ownerPrincipal} own onChanged={vi.fn()} /></OperationReceipts>;
      const { rerender } = render(view(own));
      fireEvent.click(screen.getByRole("button", { name: "Link to Jellyfin" }));
      const dialog = screen.getByRole("dialog", { name: "Link to Jellyfin" });
      fireEvent.change(within(dialog).getByLabelText("Jellyfin username"), { target: { value: "Drew" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
      let fresh = screen.getByRole("dialog", { name: "Confirm your Jellyfin password" });
      fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic" } });
      fireEvent.click(within(fresh).getByRole("button", { name: "Prove identities" }));
      fireEvent.click(await within(dialog).findByRole("button", { name: "Confirm link" }));
      fresh = screen.getByRole("dialog", { name: "Confirm link" });
      fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic" } });
      fireEvent.click(within(fresh).getByRole("button", { name: "Link account" }));
      await waitFor(() => expect(within(dialog).getByRole("region", { name: "Link result" })).toBeVisible());
      fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
      rerender(view({ ...own, source: "jellyfin", link_cleanup: linkCleanup }));
    }

    const statusTexts = () => screen.getAllByRole("status").map(node => node.textContent?.trim() ?? "");

    it("a link that moved no files does not render an empty cleanup status box", async () => {
      await linkWith([], { state: "completed", pending: 0, removed: 0, absent: 0, kept: 0 });
      expect(statusTexts().filter(text => text === "")).toEqual([]);
      expect(document.querySelector(".link-cleanup")).toBeNull();
    });

    it("a link whose old files were already absent says so instead of rendering an empty cleanup status box", async () => {
      await linkWith(preview().exports, { state: "completed", pending: 0, removed: 0, absent: 1, kept: 0 });
      expect(statusTexts().filter(text => text === "")).toEqual([]);
      expect(statusTexts()).toContain("1 old export file was already gone.");
    });
  });
});
