import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as users from "../../api/api-users";
import * as jellyfin from "../../api/api-jellyfin-accounts";
import { usePrincipal } from "../../auth/auth-gate";
import type { Principal } from "../../auth/session";
import { UsersView } from "./users-view";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-users");
vi.mock("../../api/api-jellyfin-accounts");
vi.mock("../../api/api-key");
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: vi.fn(), useOptionalAuthenticationGate: () => null }));

describe("Users directory", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [ordinaryAccount], total: 1, offset: 0, limit: 20 });
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({ running: false, accounts: [], last_run: null });
    vi.mocked(jellyfin.listBlockedJellyfinUsers).mockResolvedValue({ blocks: [] });
  });
  it("does not mount a directory or request accounts without the capability", () => {
    vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, role: "user", capabilities: new Set(["profile.view.own"]) });
    const { container } = render(<UsersView />);
    expect(container).toBeEmptyDOMElement();
    expect(users.listAccounts).not.toHaveBeenCalled();
    expect(jellyfin.readJellyfinSync).not.toHaveBeenCalled();
  });
  it("renders approved columns and backend overrides, searches and pages", async () => {
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [ordinaryAccount], total: 21, offset: 0, limit: 20 });
    render(<UsersView />);
    const table = await screen.findByRole("table");
    for (const name of ["Name", "Username", "Source", "Role", "Status", "Last login", "Created", "Overrides"]) expect(within(table).getByRole("columnheader", { name })).toBeVisible();
    expect(within(table).getByText("Media access, Filter policy")).toBeVisible();
    fireEvent.change(screen.getByRole("searchbox", { name: "Search users" }), { target: { value: "pat" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(users.listAccounts).toHaveBeenLastCalledWith("pat", 0, expect.any(AbortSignal)));
    fireEvent.click(await screen.findByRole("button", { name: "Next page" }));
    await waitFor(() => expect(users.listAccounts).toHaveBeenLastCalledWith("pat", 20, expect.any(AbortSignal)));
  });
  it("never shows another account's username as a Jellyfin account's own while that account holds it", async () => {
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [
      { ...ordinaryAccount, id: 4, username: "member4", display_name: null, source: "local" },
      { ...ordinaryAccount, id: 9, username: null, display_name: "member4", source: "jellyfin" },
    ], total: 2, offset: 0, limit: 20 });
    render(<UsersView />);
    const rows = await screen.findAllByRole("rowheader", { name: /member4/ });
    const [local, held] = rows.map((header) => header.closest("tr")!.querySelectorAll("td")[1]);
    expect(local).toHaveTextContent(/^member4$/);
    expect(held).toHaveTextContent(/^None \(Jellyfin name: member4\)$/);
    expect(screen.queryByLabelText("Jellyfin admission")).not.toBeInTheDocument();
  });
  it("shows the username Jellyfin gave a linked account, as its profile does", async () => {
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [{ ...ordinaryAccount, id: 9, username: "Casey Archive", display_name: "Casey", source: "jellyfin" }], total: 1, offset: 0, limit: 20 });
    render(<UsersView />);
    const row = (await screen.findByRole("rowheader", { name: /Casey/ })).closest("tr")!;
    expect(within(row).getByText("Casey Archive")).toBeVisible();
  });
  it("keeps per-target refused bulk results visible after the directory reload", async () => {
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [ordinaryAccount, { ...ordinaryAccount, id: 4, display_name: "Kim" }], total: 2, offset: 0, limit: 20 });
    vi.mocked(users.bulkAccountStatus).mockResolvedValue({ results: [{ user_id: 3, outcome: "applied", code: null }, { user_id: 4, outcome: "refused", code: "capability_denied" }, { user_id: 99, outcome: "refused", code: "capability_denied" }] });
    render(<UsersView />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "Select Pat" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Kim" }));
    fireEvent.click(screen.getByRole("button", { name: "Disable selected" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm" }));
    const results = await screen.findByRole("region", { name: "Bulk results" });
    await waitFor(() => expect(results).toHaveTextContent("Pat: applied"));
    expect(results).toHaveTextContent("Kim: refused (capability_denied)");
    expect(results).toHaveTextContent("Account 99: refused (capability_denied)");
    expect(users.bulkAccountStatus).toHaveBeenCalledWith([3, 4], "disabled");
    await waitFor(() => expect(users.listAccounts).toHaveBeenCalledTimes(2));
    expect(results).toBeVisible();
  });
  it("does not select owner, self, or another admin even with delegated management grants", async () => {
    vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, userId: 2 as Principal["userId"], role: "admin" });
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [ordinaryAccount, { ...ordinaryAccount, id: 1, role: "owner", display_name: "Owner" }, { ...ordinaryAccount, id: 2, role: "admin", display_name: "Self" }, { ...ordinaryAccount, id: 4, role: "admin", display_name: "Admin" }], total: 4, offset: 0, limit: 20 });
    render(<UsersView />);
    expect(await screen.findByRole("checkbox", { name: "Select Pat" })).toBeEnabled();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.queryByLabelText("Local registration")).not.toBeInTheDocument();
  });
  it("marks an account without a Jellyfin link and links it instead of enabling it", async () => {
    const unlinked = { ...ordinaryAccount, id: 5, username: null, display_name: "Sam", source: "local" as const, status: "disabled" as const, disable_cause: "unlinked" as const };
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [unlinked], total: 1, offset: 0, limit: 20 });
    render(<UsersView />);
    const row = (await screen.findByRole("rowheader", { name: /Sam/ })).closest("tr")!;
    expect(within(row).getByText("Disabled: no Jellyfin link")).toBeVisible();
    expect(within(row).getByText("No Jellyfin link")).toBeVisible();
    expect(row).not.toHaveTextContent("None");
    fireEvent.click(within(row).getByRole("checkbox", { name: "Select Sam" }));
    expect(screen.queryByRole("button", { name: "Enable selected" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create local account" })).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: "Link Sam to Jellyfin" }));
    const dialog = screen.getByRole("dialog", { name: "Link to Jellyfin" });
    expect(within(dialog).getByLabelText("Jellyfin username")).toBeVisible();
    expect(within(dialog).queryByLabelText(/Editarr password/)).not.toBeInTheDocument();
  });
  it("offers Enable selected for a linked account and no link action", async () => {
    vi.mocked(users.listAccounts).mockResolvedValue({ items: [{ ...ordinaryAccount, status: "disabled", disable_cause: "local" }], total: 1, offset: 0, limit: 20 });
    render(<UsersView />);
    const row = (await screen.findByRole("rowheader", { name: /Pat/ })).closest("tr")!;
    expect(within(row).getByText("disabled")).toBeVisible();
    expect(within(row).queryByRole("button", { name: /to Jellyfin/ })).not.toBeInTheDocument();
    fireEvent.click(within(row).getByRole("checkbox", { name: "Select Pat" }));
    expect(screen.getByRole("button", { name: "Enable selected" })).toBeVisible();
  });
  it("recovers a directory loading failure with Refresh", async () => {
    vi.mocked(users.listAccounts).mockRejectedValueOnce(new Error("Directory unavailable"));
    render(<UsersView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Directory unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Refresh users" }));
    expect(await screen.findByRole("table")).toBeVisible();
  });
});