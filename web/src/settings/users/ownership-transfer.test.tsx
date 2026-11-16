import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { transferOwnership } from "../../api/api-user-access";
import { OwnershipTransfer } from "./ownership-transfer";
import { ordinaryAccount, ownerPrincipal } from "./users.test-support";

vi.mock("../../api/api-user-access");
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => ownerPrincipal }));
describe("Ownership transfer", () => {
  afterEach(() => { cleanup(); vi.resetAllMocks(); });
  it.each([
    ["without a Jellyfin link", { source: "local", status: "disabled", disable_cause: "unlinked", successor_refusal: "successor_disabled" }],
    ["a link to a replaced server", { source: "jellyfin", successor_refusal: "successor_credentials_required" }],
    ["that is disabled", { status: "disabled", disable_cause: "local", successor_refusal: "successor_disabled" }],
  ] as const)("refuses a successor %s, as the server does", (_case, change) => {
    render(<OwnershipTransfer account={{ ...ordinaryAccount, ...change }} principal={ownerPrincipal} onTransferred={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Transfer ownership" })).toBeDisabled();
    expect(screen.getByText("The successor must be active and linked to Jellyfin.")).toBeVisible();
    expect(transferOwnership).not.toHaveBeenCalled();
  });
  it("offers an active Jellyfin-linked successor", () => {
    render(<OwnershipTransfer account={ordinaryAccount} principal={ownerPrincipal} onTransferred={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Transfer ownership" })).toBeEnabled();
  });
  it("does not delegate ownership powers", () => {
    render(<OwnershipTransfer account={ordinaryAccount} principal={{ ...ownerPrincipal, role: "admin" }} onTransferred={vi.fn()} />);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });
  it("requires fresh proof and refreshes the principal after successful transfer", async () => {
    vi.mocked(transferOwnership).mockResolvedValue({ owner_id: 3, former_owner_id: 1, former_owner_role: "admin" });
    const changed = vi.fn(); render(<OwnershipTransfer account={ordinaryAccount} principal={ownerPrincipal} onTransferred={changed} />);
    fireEvent.click(screen.getByRole("button", { name: "Transfer ownership" }));
    fireEvent.change(screen.getByLabelText("Jellyfin password"), { target: { value: "synthetic proof" } });
    fireEvent.click(screen.getByRole("dialog").querySelector("button[type=submit]")!);
    await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
    expect(transferOwnership).toHaveBeenCalledWith(3, "synthetic proof");
  });
});