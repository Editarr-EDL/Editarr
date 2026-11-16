import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as users from "../../api/api-users";
import * as jellyfin from "../../api/api-jellyfin-accounts";
import { usePrincipal } from "../../auth/auth-gate";
import { AccountDetailsDialog } from "./account-details";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-users");
vi.mock("../../api/api-key");
vi.mock("../../api/api-jellyfin-accounts");
vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn() }));

describe("Account management", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.resetAllMocks(); vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
    vi.mocked(users.readAccount).mockResolvedValue(ordinaryAccount);
    vi.mocked(jellyfin.readJellyfinSync).mockResolvedValue({ running: false, accounts: [], last_run: null });
  });
  it("offers linking, not enabling, for an account disabled because it has no Jellyfin link", async () => {
    const unlinked = { ...ordinaryAccount, username: null, source: "local" as const, status: "disabled" as const, disable_cause: "unlinked" as const, read_only_fields: [] };
    vi.mocked(users.readAccount).mockResolvedValue(unlinked);
    render(<AccountDetailsDialog target={unlinked} principal={ownerPrincipal} onClose={vi.fn()} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    expect(await screen.findByText("Disabled: no Jellyfin link")).toBeVisible();
    expect(screen.getByRole("button", { name: "Link to Jellyfin" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Enable account" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
  });
  it("enables a linked account an admin disabled", async () => {
    const disabled = { ...ordinaryAccount, status: "disabled" as const, disable_cause: "local" as const };
    vi.mocked(users.readAccount).mockResolvedValue(disabled);
    vi.mocked(users.setAccountStatus).mockResolvedValue(ordinaryAccount);
    render(<AccountDetailsDialog target={disabled} principal={ownerPrincipal} onClose={vi.fn()} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Enable account" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Enable account?" })).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Account enabled.")).toBeVisible();
    expect(users.setAccountStatus).toHaveBeenCalledWith(3, "active");
  });
  it("confirms disablement and session revocation independently without changing API keys", async () => {
    vi.mocked(users.setAccountStatus).mockResolvedValue({ ...ordinaryAccount, status: "disabled" });
    vi.mocked(users.revokeAccountSessions).mockResolvedValue({ sessions_revoked: 2 });
    render(<AccountDetailsDialog target={ordinaryAccount} principal={ownerPrincipal} onClose={vi.fn()} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable account" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Disable account?" })).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Account disabled. Data retained.")).toBeVisible();
    expect(users.setAccountStatus).toHaveBeenCalledWith(3, "disabled");
    fireEvent.click(screen.getByRole("button", { name: "Revoke sessions" }));
    fireEvent.click(within(screen.getByRole("dialog", { name: "Revoke sessions?" })).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("2 browser sessions revoked.")).toBeVisible();
    expect(users.revokeAccountSessions).toHaveBeenCalledWith(3);
    expect(users.revokeAccountKey).not.toHaveBeenCalled();
  });
  it("shows protected accounts read-only and hides all management actions", async () => {
    const account = { ...ordinaryAccount, id: 1, role: "owner" as const };
    vi.mocked(users.readAccount).mockResolvedValue(account);
    const admin = { ...ownerPrincipal, userId: 2 as typeof ownerPrincipal.userId, role: "admin" as const };
    vi.mocked(usePrincipal).mockReturnValue(admin);
    render(<AccountDetailsDialog target={account} principal={admin} onClose={vi.fn()} onChanged={vi.fn()} onDeleted={vi.fn()} />);
    await screen.findByRole("heading", { name: "Pat" });
    expect(screen.queryByRole("button", { name: "Save profile" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disable account" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke sessions" })).not.toBeInTheDocument();
  });
});