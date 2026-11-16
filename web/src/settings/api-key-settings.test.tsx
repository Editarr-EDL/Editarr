import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiKeyStatus, issueApiKey, revealApiKey, revokeApiKey } from "../api/api-key";
import { usePrincipal } from "../auth/auth-gate";
import { CAPABILITIES } from "../auth/capabilities";
import type { Principal } from "../auth/session";
import { revokeAccountKey } from "../api/api-users";
import { ordinaryAccount, ownerPrincipal } from "./users/users.test-support";
import { ApiKeySettings } from "./api-key-settings";

vi.mock("../auth/auth-gate", () => ({ usePrincipal: vi.fn(() => null) }));
vi.mock("../api/api-key", () => ({
  apiKeyStatus: vi.fn(), issueApiKey: vi.fn(), revealApiKey: vi.fn(), revokeApiKey: vi.fn(),
}));
vi.mock("../api/api-users", () => ({ revokeAccountKey: vi.fn(), issueAccountKey: vi.fn(), revealAccountKey: vi.fn() }));
const status = vi.mocked(apiKeyStatus);
const issue = vi.mocked(issueApiKey);
const reveal = vi.mocked(revealApiKey);
const revoke = vi.mocked(revokeApiKey);
const configured = { configured: true, created_at: "2026-10-13T12:00:00", revealable: true };
const secret = { api_key: "abc123", created_at: "2026-10-13T12:00:00" };

function confirmWith(password: string, action: string) {
  const dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Jellyfin password"), { target: { value: password } });
  fireEvent.click(within(dialog).getByRole("button", { name: action }));
}

describe("API key settings", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(usePrincipal).mockReturnValue(ownerPrincipal);
  });
  it("generates a key only after password confirmation and lets the user copy it", async () => {
    status.mockResolvedValue({ configured: false, created_at: null, revealable: false });
    issue.mockResolvedValue(secret);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<ApiKeySettings />);
    await waitFor(() => expect(screen.getByText("Generate API key")).toBeEnabled());
    fireEvent.click(screen.getByText("Generate API key"));
    confirmWith("my password", "Generate key");
    const input = await screen.findByLabelText("Current API key");
    expect(input).toHaveValue("abc123");
    expect(issue).toHaveBeenCalledWith("my password");
    fireEvent.click(screen.getByText("Copy key"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("abc123"));
  });
  it("never loads the key with its status and shows it only after confirmation", async () => {
    status.mockResolvedValue(configured);
    reveal.mockResolvedValue(secret);
    render(<ApiKeySettings />);
    fireEvent.click(await screen.findByText("Show API key"));
    expect(screen.queryByLabelText("Current API key")).not.toBeInTheDocument();
    confirmWith("my password", "Show key");
    expect(await screen.findByLabelText("Current API key")).toHaveValue("abc123");
    expect(reveal).toHaveBeenCalledWith("my password");
    fireEvent.click(screen.getByText("Hide key"));
    expect(screen.queryByLabelText("Current API key")).not.toBeInTheDocument();
  });
  it("keeps the dialog open and the key hidden when confirmation fails", async () => {
    status.mockResolvedValue(configured);
    reveal.mockRejectedValue(new Error("Password confirmation failed"));
    render(<ApiKeySettings />);
    fireEvent.click(await screen.findByText("Show API key"));
    confirmWith("wrong", "Show key");
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(
      "Password confirmation failed",
    );
    expect(screen.queryByLabelText("Current API key")).not.toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByLabelText("Jellyfin password")).toHaveValue("");
  });
  it("warns before replacement and revokes without a password", async () => {
    status.mockResolvedValue(configured);
    revoke.mockResolvedValue({ configured: false, created_at: null, revealable: false });
    render(<ApiKeySettings />);
    fireEvent.click(await screen.findByText("Generate new API key"));
    expect(screen.getByRole("dialog")).toHaveTextContent("stop working immediately");
    fireEvent.click(screen.getByText("Cancel"));
    expect(issue).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Revoke API key"));
    expect(within(screen.getByRole("dialog")).queryByLabelText("Jellyfin password")).toBeNull();
    fireEvent.click(screen.getByText("Revoke key"));
    await screen.findByText("API key revoked.");
    expect(screen.queryByText("API key configured")).not.toBeInTheDocument();
  });
  it("explains a key that cannot be shown", async () => {
    status.mockResolvedValue({ ...configured, revealable: false });
    render(<ApiKeySettings />);
    expect(await screen.findByText("This key cannot be shown. Generate a new key to see it here.")).toBeVisible();
    expect(screen.queryByText("Show API key")).not.toBeInTheDocument();
  });
  it("hides forbidden key controls and never fetches a key for another account", async () => {
    vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, userId: 2 as Principal["userId"], role: "admin", capabilities: new Set(["api_keys.revoke.other"]) });
    vi.mocked(revokeAccountKey).mockResolvedValue({ configured: false, created_at: null, revealable: false });
    render(<ApiKeySettings target={ordinaryAccount} />);
    expect(status).not.toHaveBeenCalled();
    expect(screen.queryByText("Show API key")).not.toBeInTheDocument();
    expect(screen.queryByText("Generate new API key")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Revoke API key"));
    fireEvent.click(screen.getByText("Revoke key"));
    await screen.findByText("API key revoked.");
    expect(revokeAccountKey).toHaveBeenCalledWith(3);
  });
  it("does not render or load a protected account's key", () => {
    vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, userId: 2 as Principal["userId"], role: "admin", capabilities: new Set(CAPABILITIES) });
    const { container } = render(<ApiKeySettings target={{ ...ordinaryAccount, id: 1, role: "owner" }} />);
    expect(container).toBeEmptyDOMElement();
    expect(status).not.toHaveBeenCalled();
  });
  it("does not fetch own key metadata without a key capability", () => {
    vi.mocked(usePrincipal).mockReturnValue({ ...ownerPrincipal, capabilities: new Set(["profile.view.own"]) });
    const { container } = render(<ApiKeySettings />);
    expect(container).toBeEmptyDOMElement();
    expect(status).not.toHaveBeenCalled();
  });
  it("keeps dialog keyboard events out of a containing account focus trap", () => {
    const parentKeyDown = vi.fn();
    render(<div onKeyDown={parentKeyDown}><ApiKeySettings target={ordinaryAccount} /></div>);
    fireEvent.click(screen.getByRole("button", { name: "Show API key" }));
    const input = screen.getByLabelText("Jellyfin password");
    input.focus();
    fireEvent.keyDown(input, { key: "Tab" });
    expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
    expect(parentKeyDown).not.toHaveBeenCalled();
  });
});
