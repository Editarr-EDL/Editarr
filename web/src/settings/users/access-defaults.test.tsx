import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as access from "../../api/api-user-access";
import { AccessDefaults } from "./access-defaults";
import { ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-user-access");
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => ownerPrincipal }));
describe("Account defaults", () => {
  afterEach(() => { cleanup(); vi.resetAllMocks(); });
  it("leaves instance default mode editing to Filters settings", () => {
    render(<AccessDefaults principal={ownerPrincipal} />);
    expect(screen.queryByText("Default filter mode")).not.toBeInTheDocument();
    expect(access.readDefaultFilterMode).not.toHaveBeenCalled();
  });
  it.each(["admin", "user"] as const)("hides owner-only defaults for %s even with forged grants", role => {
    render(<AccessDefaults principal={{ ...ownerPrincipal, role }} />);
    expect(screen.queryByText("Role permission defaults")).not.toBeInTheDocument();
    expect(screen.queryByText("Default filter mode")).not.toBeInTheDocument();
    expect(access.readRoleDefaults).not.toHaveBeenCalled();
  });
  it("hides admin media defaults immediately after removal", () => {
    const principal = { ...ownerPrincipal, role: "admin" as const };
    const { rerender } = render(<AccessDefaults principal={principal} />);
    expect(screen.getByText("Local media default")).toBeVisible();
    rerender(<AccessDefaults principal={{ ...principal, capabilities: new Set() }} />);
    expect(screen.queryByText("Local media default")).not.toBeInTheDocument();
  });
  it("saves a selected role default with fresh proof and renders the server result", async () => {
    const defaults = { user: [{ capability: "scans.start", granted: true, catalog_default: true, explicit: false }], admin: [] };
    vi.mocked(access.readRoleDefaults).mockResolvedValue(defaults);
    vi.mocked(access.saveRoleDefault).mockResolvedValue({ ...defaults, user: [{ ...defaults.user[0], granted: false }] });
    render(<AccessDefaults principal={ownerPrincipal} />);
    fireEvent.click(screen.getByText("Role permission defaults"));
    fireEvent.change(await screen.findByLabelText("Role permission value"), { target: { value: "deny" } });
    fireEvent.click(screen.getByRole("button", { name: "Save role default" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } }); fireEvent.click(screen.getByRole("button", { name: "Confirm role default" }));
    await waitFor(() => expect(access.saveRoleDefault).toHaveBeenCalledWith("user", "scans.start", false, "synthetic proof"));
    expect(await screen.findByText(/Effective role default: Deny/)).toBeVisible();
  });
  it.each([true, false])("preserves a stored explicit %s equal to the catalog through save and reload", async granted => {
    const defaults = { user: [{ capability: "scans.start", granted, catalog_default: granted, explicit: true }], admin: [] };
    vi.mocked(access.readRoleDefaults).mockResolvedValue(defaults);
    vi.mocked(access.saveRoleDefault).mockResolvedValue(defaults);
    const { unmount } = render(<AccessDefaults principal={ownerPrincipal} />);
    fireEvent.click(screen.getByText("Role permission defaults"));
    expect(await screen.findByLabelText("Role permission value")).toHaveValue(granted ? "allow" : "deny");
    fireEvent.click(screen.getByRole("button", { name: "Save role default" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm role default" }));
    await waitFor(() => expect(access.saveRoleDefault).toHaveBeenCalledWith("user", "scans.start", granted, "synthetic proof"));
    unmount();
    render(<AccessDefaults principal={ownerPrincipal} />);
    fireEvent.click(screen.getByText("Role permission defaults"));
    expect(await screen.findByLabelText("Role permission value")).toHaveValue(granted ? "allow" : "deny");
  });
  it("loads stored state when switching the role or permission and permits an intentional reset", async () => {
    const defaults = {
      user: [
        { capability: "scans.start", granted: true, catalog_default: true, explicit: false },
        { capability: "accounts.list", granted: false, catalog_default: false, explicit: true },
      ],
      admin: [
        { capability: "scans.start", granted: true, catalog_default: true, explicit: true },
        { capability: "accounts.list", granted: true, catalog_default: true, explicit: false },
      ],
    };
    vi.mocked(access.readRoleDefaults).mockResolvedValue(defaults);
    vi.mocked(access.saveRoleDefault).mockResolvedValue(defaults);
    render(<AccessDefaults principal={ownerPrincipal} />);
    fireEvent.click(screen.getByText("Role permission defaults"));
    expect(await screen.findByLabelText("Role permission value")).toHaveValue("inherit");
    fireEvent.change(screen.getByLabelText("Default permission"), { target: { value: "accounts.list" } });
    expect(screen.getByLabelText("Role permission value")).toHaveValue("deny");
    fireEvent.change(screen.getByLabelText("Default role"), { target: { value: "admin" } });
    expect(screen.getByLabelText("Role permission value")).toHaveValue("inherit");
    fireEvent.change(screen.getByLabelText("Default permission"), { target: { value: "scans.start" } });
    expect(screen.getByLabelText("Role permission value")).toHaveValue("allow");
    fireEvent.change(screen.getByLabelText("Role permission value"), { target: { value: "inherit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save role default" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm role default" }));
    await waitFor(() => expect(access.saveRoleDefault).toHaveBeenCalledWith("admin", "scans.start", null, "synthetic proof"));
  });
});
