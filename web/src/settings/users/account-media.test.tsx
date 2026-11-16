import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as access from "../../api/api-user-access";
import { AccountMedia } from "./account-media";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

const unlinked = { ...ordinaryAccount, source: "local" as const, status: "disabled" as const, disable_cause: "unlinked" as const };

vi.mock("../../api/api-user-access");
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => ownerPrincipal }));
describe("Account media controls", () => {
  afterEach(cleanup);
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(access.readUserMedia).mockResolvedValue({ linked: false, scope: { inherited: true, all_sources: true, sources: [] } }); });
  it.each([
    { role: "owner" as const, target: "user" as const, granted: true, visible: true },
    { role: "admin" as const, target: "user" as const, granted: true, visible: true },
    { role: "admin" as const, target: "user" as const, granted: false, visible: false },
    { role: "admin" as const, target: "admin" as const, granted: true, visible: false },
    { role: "admin" as const, target: "owner" as const, granted: true, visible: false },
  ])("respects $role/$target capability=$granted", ({ role, target, granted, visible }) => {
    render(<AccountMedia account={{ ...unlinked, role: target }} principal={{ ...ownerPrincipal, role, capabilities: granted ? ownerPrincipal.capabilities : new Set() }} onChanged={vi.fn()} />);
    expect(screen.queryByText("Local media access") !== null).toBe(visible);
    expect(access.readUserMedia).not.toHaveBeenCalled();
  });
  it("saves an explicit scope after fresh proof and displays returned effective sources", async () => {
    const library = { kind: "jellyfin_library" as const, id: "0123456789abcdef0123456789abcdef" };
    vi.mocked(access.saveUserMedia).mockResolvedValue({ linked: false, scope: { inherited: false, all_sources: false, sources: [library] } });
    render(<AccountMedia account={unlinked} principal={ownerPrincipal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("Local media access"));
    fireEvent.change(await screen.findByLabelText("Media scope"), { target: { value: "selected" } });
    fireEvent.change(screen.getByLabelText("Jellyfin library ID"), { target: { value: "01234567-89ab-cdef-0123-456789abcdef" } });
    fireEvent.click(screen.getByRole("button", { name: "Add library" })); fireEvent.click(screen.getByRole("button", { name: "Save media scope" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm media access" }));
    await waitFor(() => expect(access.saveUserMedia).toHaveBeenCalledWith(3, { all_sources: false, sources: [library] }, "synthetic proof"));
    expect(await screen.findByText(`Explicit. Effective sources: Jellyfin ${library.id}.`)).toBeVisible();
  });
  it("offers no media controls for a linked account, whose media Jellyfin decides", () => {
    render(<AccountMedia account={{ ...ordinaryAccount, source: "jellyfin" }} principal={ownerPrincipal} onChanged={vi.fn()} />);
    expect(screen.queryByText("Local media access")).not.toBeInTheDocument();
    expect(screen.queryByText("Unmatched-media exceptions")).not.toBeInTheDocument();
    expect(access.readUserMedia).not.toHaveBeenCalled();
  });
});