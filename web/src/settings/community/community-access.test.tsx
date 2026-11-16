import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CommunitySettings } from "./community-settings";
import * as api from "../../api/api-community";
import { ordinaryAccount, ownerPrincipal } from "../users/users.test-support";

const auth = vi.hoisted(() => ({ capabilities: new Set<string>(), role: "admin", current: true, refresh: vi.fn() }));
vi.mock("../../auth/auth-gate", () => ({
  useCan: (capability: string) => auth.capabilities.has(capability),
  usePrincipal: () => ({ userId: 1, role: auth.role }),
  useOptionalAuthenticationGate: () => ({ isAuthenticationCurrent: () => auth.current, refreshAuthenticationStatus: auth.refresh }),
}));
vi.mock("../../api/api-community", async (original) => ({
  ...await original<typeof api>(), fetchLocalCommunityConnection: vi.fn(), fetchCommunityConnection: vi.fn(),
  fetchCommunityRecovery: vi.fn(), startCommunityEnrollment: vi.fn(), disconnectCommunity: vi.fn(),
}));
const disconnected: api.CommunityConnection = { configured: true, connected: false, profile: null, pending: null };
beforeEach(() => {
  auth.capabilities = new Set(ownerPrincipal.capabilities); auth.role = "admin"; auth.current = true;
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(disconnected);
  vi.mocked(api.fetchCommunityRecovery).mockResolvedValue({ configured: false, identity: null, pending: null, completed: null });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it.each(["owner", "admin"] as const)("does not request protected %s credentials for an admin", (role) => {
  render(<CommunitySettings target={{ ...ordinaryAccount, role }} />);
  expect(api.fetchLocalCommunityConnection).not.toHaveBeenCalled();
  expect(api.fetchCommunityRecovery).not.toHaveBeenCalled();
});

it("uses named-account capabilities rather than the actor's own grants", async () => {
  auth.capabilities = new Set(["community.identity.view.other"]);
  render(<CommunitySettings target={ordinaryAccount} />);
  await screen.findByText("Not connected");
  expect(api.fetchLocalCommunityConnection).toHaveBeenCalledWith(expect.any(AbortSignal), expect.objectContaining({ userId: ordinaryAccount.id }));
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
  expect(api.fetchCommunityRecovery).not.toHaveBeenCalled();
});

it("allows self service without directory or other-account capabilities", async () => {
  auth.role = "user";
  auth.capabilities = new Set(["community.identity.view.own", "community.identity.enroll.own"]);
  render(<CommunitySettings />);
  expect(await screen.findByRole("button", { name: "Connect with Discord" })).toBeEnabled();
  expect(api.fetchCommunityRecovery).not.toHaveBeenCalled();
});

it.each([false, true])("an owner manages an admin only with the admin-management grant=%s", async granted => {
  auth.role = "owner";
  if (!granted) auth.capabilities.delete("accounts.admins.manage");
  render(<CommunitySettings target={{ ...ordinaryAccount, role: "admin" }} />);
  if (granted) expect(await screen.findByRole("button", { name: "Connect with Discord" })).toBeEnabled();
  else expect(api.fetchLocalCommunityConnection).not.toHaveBeenCalled();
});

it("clears handoff drafts and aborts the old target when the account changes", async () => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue({ ...disconnected,
    pending: { stateId: null, expiresAt: 1900000000, awaitingConfirmation: true } });
  const view = render(<CommunitySettings target={ordinaryAccount} />);
  fireEvent.change(await screen.findByLabelText("Discord confirmation URL or enrollment reference"), { target: { value: "private-draft" } });
  const options = vi.mocked(api.fetchLocalCommunityConnection).mock.calls[0][1]!;
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(disconnected);
  view.rerender(<CommunitySettings target={{ ...ordinaryAccount, id: 8 }} />);
  await screen.findByText("Not connected");
  expect(options.signal!.aborted).toBe(true);
  expect(screen.queryByDisplayValue("private-draft")).not.toBeInTheDocument();
});

it.each(["unmount", "auth-end"])("does not reread credentials after %s during a failed mutation", async (change) => {
  let reject!: (error: Error) => void;
  vi.mocked(api.startCommunityEnrollment).mockReturnValue(new Promise((_resolve, rejectRequest) => { reject = rejectRequest; }));
  const view = render(<CommunitySettings />);
  fireEvent.click(await screen.findByRole("button", { name: "Connect with Discord" }));
  if (change === "unmount") view.unmount(); else auth.current = false;
  reject(new api.CommunityApiError("network_unavailable", true, 503));
  await waitFor(() => expect(api.startCommunityEnrollment).toHaveBeenCalledTimes(1));
  await new Promise(resolve => setTimeout(resolve, 0));
  expect(api.fetchLocalCommunityConnection).toHaveBeenCalledTimes(1);
});

it("removes private connection content when a target is denied", async () => {
  vi.mocked(api.startCommunityEnrollment).mockRejectedValue(new api.CommunityApiError("not_found", false, 404));
  render(<CommunitySettings target={ordinaryAccount} />);
  fireEvent.click(await screen.findByRole("button", { name: "Connect with Discord" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer available");
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
  expect(api.fetchLocalCommunityConnection).toHaveBeenCalledTimes(1);
});

it.each([403, 404])("clears cached identity and recovery panels when refresh returns %s", async status => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValueOnce({ ...disconnected,
    identity: { installationId: "private-target-installation", keyId: "private-target-key", lastKnownKeyState: "unenrolled" } });
  render(<CommunitySettings target={ordinaryAccount} />);
  await screen.findByText("private-target-installation");
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh connection" })).toBeEnabled());
  vi.mocked(api.fetchLocalCommunityConnection).mockRejectedValueOnce(new api.CommunityApiError("not_found", false, status));
  fireEvent.click(screen.getByRole("button", { name: "Refresh connection" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("private-target-installation")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Lost-key recovery" })).not.toBeInTheDocument();
  expect(auth.refresh).toHaveBeenCalledOnce();
});

it.each([403, 404, 503])("handles initial signed connection failure %s", async status => {
  const connected = { ...disconnected, connected: true,
    identity: { installationId: "private-target-installation", keyId: "private-target-key", lastKnownKeyState: "active" } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(connected);
  vi.mocked(api.fetchCommunityConnection).mockRejectedValue(new api.CommunityApiError(
    status === 403 ? "protected_account" : status === 404 ? "not_found" : "network_unavailable", status === 503, status,
  ));
  render(<CommunitySettings target={ordinaryAccount} />);
  await screen.findByRole("alert");
  if (status === 503) {
    expect(screen.getByText("private-target-installation")).toBeVisible();
    expect(auth.refresh).not.toHaveBeenCalled();
  } else {
    expect(screen.queryByText("private-target-installation")).not.toBeInTheDocument();
    expect(screen.queryByText("private-target-key")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Disconnect community" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Lost-key recovery" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recover import receipts" })).not.toBeInTheDocument();
    expect(auth.refresh).toHaveBeenCalledOnce();
  }
});

it.each([403, 404])("clears protected state when mutation reconciliation returns %s", async status => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValueOnce({ ...disconnected,
    identity: { installationId: "private-target-installation", keyId: "private-target-key", lastKnownKeyState: "unenrolled" } });
  vi.mocked(api.fetchLocalCommunityConnection).mockRejectedValueOnce(new api.CommunityApiError(
    status === 403 ? "protected_account" : "not_found", false, status,
  ));
  vi.mocked(api.startCommunityEnrollment).mockRejectedValue(new api.CommunityApiError("network_unavailable", true, 503));
  render(<CommunitySettings target={ordinaryAccount} />);
  await screen.findByText("private-target-installation");
  await waitFor(() => expect(screen.getByRole("button", { name: "Connect with Discord" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Connect with Discord" }));
  await screen.findByRole("alert");
  await waitFor(() => expect(api.fetchLocalCommunityConnection).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("private-target-installation")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Lost-key recovery" })).not.toBeInTheDocument();
  expect(auth.refresh).toHaveBeenCalledOnce();
});