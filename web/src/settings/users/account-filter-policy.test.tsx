import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as access from "../../api/api-user-access";
import { fetchFilterDefaultsSettings } from "../../api/api-service";
import { AccountFilterPolicy } from "./account-filter-policy";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-user-access");
vi.mock("../../api/api-service", () => ({ fetchFilterDefaultsSettings: vi.fn() }));
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => ownerPrincipal }));
const view: access.FilterPreferences = { user_id: 3, mode: "live", mode_locked: false, revision: 7, policy_revision: 4, instance_revision: 2, rules: [], severities: [], enforced_rules: [], enforced_severities: [] };
describe("Assigned filter policy", () => {
  afterEach(cleanup);
  beforeEach(() => { vi.resetAllMocks(); vi.mocked(access.readFilterPreferences).mockResolvedValue(view); vi.mocked(fetchFilterDefaultsSettings).mockResolvedValue({ revision: 2, selection_rules: [], severity_defaults: [{ subcategory: "nudity", severity: "high" }] }); });
  it("lets a permitted admin assign modes but not enforcement and hides after capability removal", async () => {
    const principal = { ...ownerPrincipal, role: "admin" as const, capabilities: new Set(["filters.mode.assign.other"] as const) };
    const { rerender } = render(<AccountFilterPolicy account={ordinaryAccount} principal={principal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("Filter mode and policy"));
    const assigned = await screen.findByLabelText("Assigned filter mode");
    expect([...assigned.querySelectorAll("option")].map(option => option.value)).toEqual(["live", "copy"]);
    expect(screen.queryByRole("button", { name: "Save enforced policy" })).not.toBeInTheDocument();
    expect(fetchFilterDefaultsSettings).not.toHaveBeenCalled();
    rerender(<AccountFilterPolicy account={ordinaryAccount} principal={{ ...principal, capabilities: new Set() }} onChanged={vi.fn()} />);
    expect(screen.queryByText("Filter mode and policy")).not.toBeInTheDocument();
  });
  it("uses the returned revision to assign a mode without recomputing effective values", async () => {
    vi.mocked(access.saveFilterMode).mockResolvedValue({ ...view, mode: "copy" });
    render(<AccountFilterPolicy account={ordinaryAccount} principal={ownerPrincipal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("Filter mode and policy"));
    fireEvent.change(await screen.findByLabelText("Assigned filter mode"), { target: { value: "copy" } });
    fireEvent.click(screen.getByRole("button", { name: "Save filter mode" }));
    await waitFor(() => expect(access.saveFilterMode).toHaveBeenCalledWith(3, 7, "copy"));
    expect(await screen.findByText(/Filter mode: copy/)).toBeVisible();
  });
  it("enforces selected instance severity IDs with fresh authentication and the policy revision", async () => {
    vi.mocked(access.saveFilterPolicy).mockResolvedValue({ ...view, policy_revision: 5, enforced_severities: ["nudity"], mode_locked: true });
    render(<AccountFilterPolicy account={ordinaryAccount} principal={ownerPrincipal} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByText("Filter mode and policy"));
    fireEvent.click(await screen.findByText("Enforced instance severities"));
    fireEvent.click(screen.getByLabelText("nudity: high")); fireEvent.click(screen.getByRole("button", { name: "Save enforced policy" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm enforced policy" }));
    await waitFor(() => expect(access.saveFilterPolicy).toHaveBeenCalledWith(3, 4, [], ["nudity"], "synthetic proof"));
    expect(await screen.findByText(/Self-service mode locked/)).toBeVisible();
  });
});