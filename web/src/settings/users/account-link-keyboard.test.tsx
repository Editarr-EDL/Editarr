import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOptionalAuthenticationGate, usePrincipal } from "../../auth/auth-gate";
import { ModalDialog } from "../../ui/ModalDialog";
import { AccountLinkSection } from "./account-link-section";
import { ownerPrincipal, unlinkedAccount } from "./users.test-support";

vi.mock("../../auth/auth-gate", async original => ({ ...await original<typeof import("../../auth/auth-gate")>(), usePrincipal: vi.fn(), useOptionalAuthenticationGate: vi.fn() }));

const closeDetails = vi.fn();

function openInsideAccountDetails() {
  render(<ModalDialog labelledBy="details" onClose={closeDetails}>
    <h2 id="details">Account details</h2>
    <AccountLinkSection account={unlinkedAccount} principal={ownerPrincipal} own={false} onChanged={vi.fn()} />
    <button type="button">Disable account</button>
  </ModalDialog>);
  fireEvent.click(screen.getByRole("button", { name: "Link to Jellyfin" }));
  return screen.getByRole("dialog", { name: "Link to Jellyfin" });
}

function askForProof(link: HTMLElement) {
  fireEvent.change(within(link).getByLabelText("Jellyfin username"), { target: { value: "Casey" } });
  fireEvent.click(within(link).getByRole("button", { name: "Review impact" }));
  return screen.getByRole("dialog", { name: "Confirm your Jellyfin password" });
}

function tabs(count: number, shiftKey = false) {
  return Array.from({ length: count }, () => {
    fireEvent.keyDown(document.activeElement!, { key: "Tab", shiftKey });
    const active = document.activeElement as HTMLElement;
    return active.getAttribute("aria-label") || active.textContent || active.closest("label")?.textContent || active.tagName;
  });
}

describe("Account link keyboard focus", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
    vi.mocked(useOptionalAuthenticationGate).mockReturnValue(null);
  });

  it("cycles Tab and Shift+Tab through the password prompt only, never the obscured account actions", () => {
    const fresh = askForProof(openInsideAccountDetails());
    within(fresh).getByLabelText("Jellyfin password").focus();
    expect(tabs(6)).toEqual(["Cancel", "Prove identities", "Jellyfin password", "Cancel", "Prove identities", "Jellyfin password"]);
    expect(tabs(3, true)).toEqual(["Prove identities", "Cancel", "Jellyfin password"]);
  });

  it("returns stray focus in the link dialog to the topmost prompt", () => {
    const link = openInsideAccountDetails();
    const fresh = askForProof(link);
    within(link).getByRole("button", { name: "Close" }).focus();
    fireEvent.keyDown(document.activeElement!, { key: "Tab" });
    expect(fresh).toContainElement(document.activeElement as HTMLElement);
  });

  it("keeps a busy confirmation open and the account details behind it on Escape", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    const link = openInsideAccountDetails();
    const fresh = askForProof(link);
    fireEvent.change(within(fresh).getByLabelText("Jellyfin password"), { target: { value: "owner synthetic password" } });
    fireEvent.click(within(fresh).getByRole("button", { name: "Prove identities" }));
    expect(within(fresh).getByRole("button", { name: "Prove identities" })).toBeDisabled();
    fresh.focus();
    tabs(2);
    expect(document.activeElement).toBe(fresh);
    fireEvent.keyDown(fresh, { key: "Escape" });
    expect(screen.getByRole("dialog", { name: "Confirm your Jellyfin password" })).toBe(fresh);
    expect(screen.getByRole("dialog", { name: "Link to Jellyfin" })).toBe(link);
    expect(closeDetails).not.toHaveBeenCalled();
  });
});
