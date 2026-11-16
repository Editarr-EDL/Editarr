import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as jellyfin from "../../api/api-jellyfin-accounts";
import { usePrincipal } from "../../auth/auth-gate";
import type { Capability } from "../../auth/capabilities";
import { BlockedJellyfinUsers } from "./jellyfin-blocks";
import { ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-jellyfin-accounts");
vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn() }));

const casey = { server_id: "s".repeat(32), jellyfin_user_id: "c".repeat(32), name: "Casey", blocked_by: 1, blocked_at: "2026-11-04T06:00:00Z" };

describe("Blocked Jellyfin users", () => {
  afterEach(cleanup);
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal); });

  it("lets the owner unblock with a fresh password", async () => {
    vi.mocked(jellyfin.listBlockedJellyfinUsers).mockResolvedValue({ blocks: [casey] });
    vi.mocked(jellyfin.unblockJellyfinUser).mockResolvedValue({ blocks: [] });
    render(<BlockedJellyfinUsers principal={ownerPrincipal} revision={0} />);
    const section = await screen.findByRole("region", { name: "Blocked Jellyfin users" });
    expect(section).toHaveTextContent("Casey");
    fireEvent.click(within(section).getByRole("button", { name: "Unblock Casey" }));
    const fresh = screen.getByRole("dialog", { name: "Unblock Casey" });
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic password" } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Unblock" }));
    expect(await within(section).findByRole("status")).toHaveTextContent("Casey is unblocked.");
    expect(jellyfin.unblockJellyfinUser).toHaveBeenCalledWith(casey, "owner synthetic password");
    expect(within(section).queryByRole("button", { name: "Unblock Casey" })).not.toBeInTheDocument();
  });

  it("shows admins the list without an Unblock action", async () => {
    vi.mocked(jellyfin.listBlockedJellyfinUsers).mockResolvedValue({ blocks: [casey] });
    const admin = { ...ownerPrincipal, role: "admin" as const, capabilities: new Set<Capability>(["accounts.list", "accounts.delete.other"]) };
    render(<BlockedJellyfinUsers principal={admin} revision={0} />);
    const section = await screen.findByRole("region", { name: "Blocked Jellyfin users" });
    expect(section).toHaveTextContent("Casey");
    expect(within(section).queryByRole("button", { name: /Unblock/ })).not.toBeInTheDocument();
  });

  it("stays hidden while nobody is blocked", async () => {
    vi.mocked(jellyfin.listBlockedJellyfinUsers).mockResolvedValue({ blocks: [] });
    render(<BlockedJellyfinUsers principal={ownerPrincipal} revision={0} />);
    await vi.waitFor(() => expect(jellyfin.listBlockedJellyfinUsers).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Blocked Jellyfin users" })).not.toBeInTheDocument();
  });
});
