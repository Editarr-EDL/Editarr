import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as access from "../../api/api-user-access";
import { ApiError } from "../../api/api-transport";
import { BulkAccess } from "./bulk-access";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-user-access");
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => ownerPrincipal, useOptionalAuthenticationGate: () => null }));
describe("Bulk access outcomes", () => {
  afterEach(() => { cleanup(); vi.resetAllMocks(); });
  const protectedAccount = { ...ordinaryAccount, id: 4, role: "admin" as const, display_name: "Protected" };
  it("preserves every mixed backend role outcome without silently dropping a target", async () => {
    vi.mocked(access.bulkRoles).mockResolvedValue({ results: [{ user_id: 3, outcome: "applied", code: null }, { user_id: 4, outcome: "refused", code: "protected_account" }] });
    render(<BulkAccess selected={[ordinaryAccount, protectedAccount]} principal={ownerPrincipal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Assign selected roles" })); fireEvent.click(screen.getByRole("button", { name: "Confirm selected changes" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } }); fireEvent.click(screen.getByRole("button", { name: "Apply selected changes" }));
    expect(await screen.findByRole("region", { name: "Bulk access results" })).toHaveTextContent("Protected: refused (protected_account)");
    expect(access.bulkRoles).toHaveBeenCalledWith([3, 4], "user", "synthetic proof");
  });
  it("reads each mode revision and reports protected-target refusal independently", async () => {
    vi.mocked(access.readFilterPreferences).mockResolvedValueOnce({ revision: 12 } as access.FilterPreferences).mockRejectedValueOnce(new ApiError("Protected", 403, "protected_account"));
    vi.mocked(access.saveFilterMode).mockResolvedValue({} as access.FilterPreferences);
    render(<BulkAccess selected={[ordinaryAccount, protectedAccount]} principal={{ ...ownerPrincipal, role: "admin" }} onChanged={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Assign selected roles" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Set selected filter modes" })); fireEvent.click(screen.getByRole("button", { name: "Confirm selected changes" }));
    await waitFor(() => expect(access.saveFilterMode).toHaveBeenCalledWith(3, 12, "live"));
    expect(await screen.findByRole("region", { name: "Bulk access results" })).toHaveTextContent("Protected: refused (protected_account)");
    expect(access.saveFilterMode).toHaveBeenCalledTimes(1);
  });
  it.each([
    new TypeError("Failed to fetch"),
    new DOMException("The operation was aborted", "AbortError"),
  ])("reports an unknown outcome when a committed mode change loses its response (%s)", async reason => {
    const stored = { mode: "live" };
    vi.mocked(access.readFilterPreferences).mockResolvedValue({ revision: 12 } as access.FilterPreferences);
    vi.mocked(access.saveFilterMode).mockImplementation(async () => {
      stored.mode = "copy";
      throw reason;
    });
    render(<BulkAccess selected={[ordinaryAccount]} principal={ownerPrincipal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Set selected filter modes" }));
    fireEvent.change(screen.getByLabelText("Selected accounts filter mode"), { target: { value: "copy" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm selected changes" }));
    const results = await screen.findByRole("region", { name: "Bulk access results" });
    expect(stored.mode).toBe("copy");
    expect(results).toHaveTextContent("Pat: outcome unknown");
    expect(results).not.toHaveTextContent("refused");
    expect(results).not.toHaveTextContent("unchanged");
  });
});
