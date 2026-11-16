import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as deletion from "../../api/api-account-deletion";
import * as jellyfin from "../../api/api-jellyfin-accounts";
import { ApiError } from "../../api/api-transport";
import * as users from "../../api/api-users";
import { usePrincipal } from "../../auth/auth-gate";
import type { Principal } from "../../auth/session";
import { deletionPreview, deletionResult, summary } from "./account-deletion.test-support";
import { BulkAccountDeletion } from "./account-deletion-bulk";
import { UsersView } from "./users-view";
import { ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-users");
vi.mock("../../api/api-account-deletion");
vi.mock("../../api/api-jellyfin-accounts");
vi.mock("../../api/api-key");
vi.mock("../../routes", () => ({ navigate: vi.fn() }));
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: vi.fn(), useOptionalAuthenticationGate: () => null }));

const adminPrincipal: Principal = { ...ownerPrincipal, userId: 2 as Principal["userId"], role: "admin", capabilities: new Set([...ownerPrincipal.capabilities].filter(capability => capability !== "accounts.admins.manage")) };
const accounts = [summary(1, "Olive", { role: "owner" }), summary(2, "Ada", { role: "admin" }), summary(3, "Pat"), summary(4, "Kim"), summary(5, "Lee", { role: "admin" }), summary(6, "Robin")];
const PASSWORD = "admin synthetic password";

async function confirmBulk(count: number) {
  fireEvent.click(await screen.findByRole("button", { name: `Delete ${count} ${count === 1 ? "account" : "accounts"}` }));
  const fresh = screen.getByRole("dialog", { name: "Confirm deleting selected accounts" });
  fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: PASSWORD } });
  fireEvent.click(within(fresh).getByRole("button", { name: `Delete ${count} ${count === 1 ? "account" : "accounts"}` }));
}

describe("Account deletion entry points", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(adminPrincipal);
    vi.mocked(users.listAccounts).mockResolvedValue({ items: accounts, total: accounts.length, offset: 0, limit: 20 });
    vi.mocked(jellyfin.listBlockedJellyfinUsers).mockResolvedValue({ blocks: [] });
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({ running: false, accounts: [], last_run: null });
  });

  it("blocks the Jellyfin users of linked selected accounts and leaves local ones alone", async () => {
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async (id: number) => deletionPreview({ user_id: id, confirmation: String(id).repeat(64), credentials: { sessions: 0, api_key: false, linked: id === 3 } }));
    vi.mocked(deletion.bulkDeleteAccounts).mockResolvedValue([
      { user_id: 3, outcome: "applied", code: null, result: deletionResult({ user_id: 3, identity_released: true, jellyfin_blocked: true }) },
      { user_id: 4, outcome: "applied", code: null, result: deletionResult({ user_id: 4 }) },
    ]);
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Kim" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const choice = await within(dialog).findByRole("group", { name: "After deletion, their Jellyfin users" });
    fireEvent.click(within(choice).getByRole("radio", { name: /Block them/ }));
    await confirmBulk(2);
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion of Pat" })).toHaveTextContent("Jellyfin user blocked"));
    expect(vi.mocked(deletion.bulkDeleteAccounts).mock.calls[0][0].map(target => [target.userId, target.jellyfinUser])).toEqual([[3, "block"], [4, "allow"]]);
  });

  it("offers row deletion only for accounts an admin may delete", async () => {
    render(<UsersView />);
    expect(await screen.findByRole("button", { name: "Delete Pat" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: /^Delete / }).map(button => button.getAttribute("aria-label"))).toEqual(["Delete Pat", "Delete Kim", "Delete Robin"]);
    expect(screen.queryByRole("checkbox", { name: "Select Olive" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select Lee" })).not.toBeInTheDocument();
  });

  it("lets the owner delete an admin but never itself", async () => {
    vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, userId: 1 as Principal["userId"] });
    render(<UsersView />);
    await screen.findByRole("button", { name: "Delete Ada" });
    expect(screen.queryByRole("button", { name: "Delete Olive" })).not.toBeInTheDocument();
  });

  it("reports partial bulk results per target and re-reviews only the stale one", async () => {
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async id => deletionPreview({ user_id: id, confirmation: String(id).repeat(64) }));
    vi.mocked(deletion.bulkDeleteAccounts)
      .mockResolvedValueOnce([
        { user_id: 3, outcome: "applied", code: null, result: deletionResult({ user_id: 3 }) },
        { user_id: 4, outcome: "refused", code: "stale_account_deletion", result: null },
        { user_id: 6, outcome: "refused", code: "export_write_failed", result: null },
      ])
      .mockResolvedValueOnce([{ user_id: 4, outcome: "applied", code: null, result: deletionResult({ user_id: 4, reviews_retained: ["review-9"] }) }]);
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Kim" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Robin" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    await confirmBulk(3);
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion of Pat" })).toHaveTextContent("Deleted."));
    expect(vi.mocked(deletion.bulkDeleteAccounts).mock.calls[0]).toEqual([[3, 4, 6].map(id => expect.objectContaining({ userId: id, disposition: { mode: "retain" }, preview: expect.objectContaining({ confirmation: String(id).repeat(64) }) })), PASSWORD]);
    const kim = within(dialog).getByRole("region", { name: "Deletion of Kim" });
    expect(kim).toHaveTextContent("The account changed after this preview. Nothing changed. Review the current impact and confirm again.");
    expect(within(dialog).getByRole("region", { name: "Deletion of Robin" })).toHaveTextContent("Not changed. A moved export file could not be written.");
    expect(within(dialog).queryByRole("button", { name: "Change choice" })).not.toBeInTheDocument();
    const outcomes = screen.getByRole("region", { name: "Account deletion results" });
    expect(outcomes).toHaveTextContent("Pat: deleted");
    expect(outcomes).toHaveTextContent("2 reviews kept unchanged.");
    expect(outcomes).toHaveTextContent("Kim: not changed");
    expect(outcomes).toHaveTextContent("Robin: not changed");
    await waitFor(() => expect(deletion.previewAccountDeletion).toHaveBeenCalledTimes(4));
    await confirmBulk(1);
    await waitFor(() => expect(kim).toHaveTextContent("Deleted."));
    expect(vi.mocked(deletion.bulkDeleteAccounts).mock.calls[1][0].map(target => [target.userId, target.preview.confirmation])).toEqual([[4, "4".repeat(64)]]);
    expect(screen.getByRole("region", { name: "Account deletion results" })).toHaveTextContent("Kim: deleted");
  });

  it("offers bulk confirmation only after every preview settles, and submits exactly the reviewed targets", async () => {
    let releaseKim: () => void = () => undefined;
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async id => {
      if (id === 4) await new Promise<void>(resolve => { releaseKim = resolve; });
      return deletionPreview({ user_id: id, confirmation: String(id).repeat(64) });
    });
    vi.mocked(deletion.bulkDeleteAccounts).mockImplementation(async targets => targets.map(target => ({ user_id: target.userId, outcome: "applied" as const, code: null, result: deletionResult({ user_id: target.userId }) })));
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Kim" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion of Pat" })).toHaveTextContent("2 saved reviews stay unchanged"));
    await waitFor(() => expect(deletion.previewAccountDeletion).toHaveBeenCalledWith(4, { mode: "retain" }, expect.anything()));
    expect(within(dialog).getByRole("region", { name: "Deletion of Kim" })).toHaveTextContent("Checking what this changes...");
    expect(within(dialog).queryByRole("button", { name: /^Delete \d/ })).not.toBeInTheDocument();
    releaseKim();
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion of Kim" })).toHaveTextContent("2 saved reviews stay unchanged"));
    await confirmBulk(2);
    await waitFor(() => expect(within(dialog).getByRole("region", { name: "Deletion of Kim" })).toHaveTextContent("Deleted."));
    expect(vi.mocked(deletion.bulkDeleteAccounts).mock.calls).toHaveLength(1);
    expect(vi.mocked(deletion.bulkDeleteAccounts).mock.calls[0][0].map(target => [target.userId, target.preview.confirmation])).toEqual([[3, "3".repeat(64)], [4, "4".repeat(64)]]);
  });

  it("reports an unknown bulk outcome and rereads Users when the connection drops", async () => {
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async id => deletionPreview({ user_id: id }));
    vi.mocked(deletion.bulkDeleteAccounts).mockRejectedValue(new TypeError("Failed to fetch"));
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const reads = vi.mocked(users.listAccounts).mock.calls.length;
    await confirmBulk(1);
    const pat = within(dialog).getByRole("region", { name: "Deletion of Pat" });
    await waitFor(() => expect(pat).toHaveTextContent("Editarr did not confirm whether Pat was deleted."));
    expect(pat).not.toHaveTextContent(/Not changed|Failed to fetch/);
    expect(screen.getByRole("region", { name: "Account deletion results" })).toHaveTextContent("Pat: outcome unknown");
    await waitFor(() => expect(vi.mocked(users.listAccounts).mock.calls.length).toBeGreaterThan(reads));
  });

  it("reports an unknown outcome for a target Editarr answers busy, and rereads Users", async () => {
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async id => deletionPreview({ user_id: id }));
    vi.mocked(deletion.bulkDeleteAccounts)
      .mockResolvedValueOnce([
        { user_id: 3, outcome: "refused", code: "database_busy", result: null },
        { user_id: 4, outcome: "refused", code: "export_write_failed", result: null },
      ]);
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Kim" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const reads = vi.mocked(users.listAccounts).mock.calls.length;
    await confirmBulk(2);
    const pat = within(dialog).getByRole("region", { name: "Deletion of Pat" });
    await waitFor(() => expect(pat).toHaveTextContent("Editarr did not confirm whether Pat was deleted."));
    expect(pat).not.toHaveTextContent(/Not changed|busy/);
    expect(within(dialog).getByRole("region", { name: "Deletion of Kim" })).toHaveTextContent("Not changed. A moved export file could not be written.");
    const outcomes = screen.getByRole("region", { name: "Account deletion results" });
    expect(outcomes).toHaveTextContent("Pat: outcome unknown");
    expect(outcomes).toHaveTextContent("Kim: not changed");
    await waitFor(() => expect(vi.mocked(users.listAccounts).mock.calls.length).toBeGreaterThan(reads));
  });

  it("reports an unknown bulk outcome and rereads Users when the whole request answers busy", async () => {
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async id => deletionPreview({ user_id: id }));
    vi.mocked(deletion.bulkDeleteAccounts).mockRejectedValue(new ApiError("The database is busy; retry shortly", 503, "database_busy"));
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    const reads = vi.mocked(users.listAccounts).mock.calls.length;
    await confirmBulk(1);
    const pat = within(dialog).getByRole("region", { name: "Deletion of Pat" });
    await waitFor(() => expect(pat).toHaveTextContent("Editarr did not confirm whether Pat was deleted."));
    expect(pat).not.toHaveTextContent(/Not changed|busy/);
    expect(screen.getByRole("region", { name: "Account deletion results" })).toHaveTextContent("Pat: outcome unknown");
    await waitFor(() => expect(vi.mocked(users.listAccounts).mock.calls.length).toBeGreaterThan(reads));
  });

  it("keeps protected selections out of bulk deletion and says why", async () => {
    vi.mocked(deletion.previewAccountDeletion).mockImplementation(async id => deletionPreview({ user_id: id }));
    render(<BulkAccountDeletion selected={[accounts[0], accounts[2], accounts[4], accounts[1]]} principal={adminPrincipal} onDeleted={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
    const dialog = screen.getByRole("dialog", { name: "Delete selected accounts" });
    expect(dialog).toHaveTextContent("Pat. Each account is checked and changed on its own.");
    expect(dialog).toHaveTextContent("Not included: Olive, Lee, Ada. The owner, your own account, and accounts you can't manage are protected.");
    fireEvent.click(within(dialog).getByRole("radio", { name: /Keep data/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Review impact" }));
    await within(dialog).findByRole("region", { name: "Deletion of Pat" });
    expect(vi.mocked(deletion.previewAccountDeletion).mock.calls.map(call => call[0])).toEqual([3]);
  });

  it("offers no bulk deletion when every selected account is protected", () => {
    render(<BulkAccountDeletion selected={[accounts[0], accounts[4]]} principal={adminPrincipal} onDeleted={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Delete selected" })).not.toBeInTheDocument();
  });

  it.each([
    [1, "Olive is the owner and can't be deleted."],
    [5, "Lee is an admin. Only the owner can delete an admin account."],
    [2, "You can't delete your own account."],
  ])("refuses the deep link to protected account %i without a preview", async (id, message) => {
    vi.mocked(users.readAccount).mockResolvedValue({ ...accounts.find(account => account.id === id)!, read_only_fields: [], available_languages: [], link_cleanup: null, successor_refusal: null, email: null, timezone: null, language: null });
    render(<UsersView deleteAccountId={id} />);
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deletion.previewAccountDeletion).not.toHaveBeenCalled();
  });

  it("opens the deletion dialog from the deep link for an ordinary account, and reports an unknown one", async () => {
    vi.mocked(users.readAccount).mockImplementation(async id => {
      if (id !== 3) throw new ApiError("Not found", 404, "not_found");
      return { ...accounts[2], read_only_fields: [], available_languages: [], link_cleanup: null, successor_refusal: null, email: null, timezone: null, language: null };
    });
    const { unmount } = render(<UsersView deleteAccountId={3} />);
    expect(await screen.findByRole("dialog", { name: "Delete Pat" })).toBeVisible();
    unmount();
    render(<UsersView deleteAccountId={77} />);
    expect(await screen.findByText("That account doesn't exist or you can't see it.")).toBeVisible();
  });
});
