import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as access from "../../api/api-user-access";
import { RolePermissions } from "./role-permissions";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-user-access");
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => ownerPrincipal }));
describe("Role and permission editor", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(access.readPermissions).mockResolvedValue({ role: "user", permission_revision: 2, overrides: { "scans.start": "deny" }, effective: ["profile.view.own"] });
    vi.mocked(access.readRoleDefaults).mockResolvedValue({ user: [{ capability: "scans.start", granted: true, catalog_default: true, explicit: false }], admin: [] });
  });
  it.each(["admin", "user"] as const)("hides owner controls from %s even with forged owner capabilities", role => {
    render(<RolePermissions account={ordinaryAccount} principal={{ ...ownerPrincipal, role }} onChanged={vi.fn()} />);
    expect(screen.queryByRole("region")).not.toBeInTheDocument(); expect(access.readPermissions).not.toHaveBeenCalled();
  });
  it("renders the server's effective denial separately from inheritance and saves only the selected override", async () => {
    vi.mocked(access.savePermissions).mockResolvedValue({ role: "user", permission_revision: 3, overrides: {}, effective: ["scans.start"] });
    render(<RolePermissions account={ordinaryAccount} principal={ownerPrincipal} onChanged={vi.fn()} />);
    expect(await screen.findByText(/Effective: Deny/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("Permission override"), { target: { value: "inherit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save permission override" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic owner password" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(access.savePermissions).toHaveBeenCalledWith(3, { "scans.start": "inherit" }, "synthetic owner password"));
    expect(await screen.findByText(/Effective: Allow/)).toBeVisible();
  });
  it("assigns a linked account's role without offering owner, a fourth role, or Jellyfin role sync", async () => {
    const account = { ...ordinaryAccount, source: "jellyfin" as const };
    vi.mocked(access.assignAccountRole).mockResolvedValue({ ...account, role: "admin" });
    const changed = vi.fn(); render(<RolePermissions account={account} principal={ownerPrincipal} onChanged={changed} />);
    await screen.findByLabelText("Permission");
    expect([...screen.getByLabelText<HTMLSelectElement>("Assigned role").options].map(option => option.value)).toEqual(["user", "admin"]);
    expect(screen.queryByRole("button", { name: /Jellyfin role/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Assigned role"), { target: { value: "admin" } });
    fireEvent.click(screen.getByRole("button", { name: "Assign role" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic owner password" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(access.assignAccountRole).toHaveBeenCalledWith(account.id, "admin", "synthetic owner password"));
    expect(changed).toHaveBeenCalledWith(expect.objectContaining({ role: "admin" }));
  });
});
