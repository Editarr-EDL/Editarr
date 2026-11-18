import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CommunitySettings } from "./community-settings";
import * as api from "../../api/api-community";
import { handoffState } from "./community-state";

vi.mock("../../api/api-community", async (original) => ({
  ...await original<typeof api>(), fetchCommunityConnection: vi.fn(), fetchLocalCommunityConnection: vi.fn(),
  startCommunityEnrollment: vi.fn(), completeCommunityEnrollment: vi.fn(),
  cancelCommunityEnrollment: vi.fn(), renameCommunityProfile: vi.fn(), cancelCommunityProfileUpdate: vi.fn(),
  rotateCommunityKey: vi.fn(), reconcileCommunityKey: vi.fn(), retryCommunityKey: vi.fn(),
}));
vi.mock("../../auth/auth-gate", async (original) => ({
  ...await original<typeof import("../../auth/auth-gate")>(),
  useCan: () => true,
  useOptionalAuthenticationGate: () => ({
    status: {
      state: "authenticated", setup_required: false, administrator: null,
      configuration: { method: "forms", authentication_required: "enabled", method_externally_overridden: false, authentication_required_externally_overridden: false },
      csrf_token: "csrf-token", restart_required: false,
    },
    acceptAuthenticationStatus: vi.fn(), retryAuthenticationStatus: vi.fn(),
  }),
}));
const disconnected: api.CommunityConnection = { configured: true, connected: false,
  serviceUrl: "https://community.test", profile: null, pending: null,
  identity: { installationId: "installation-1", keyId: "sha256:" + "a".repeat(64), lastKnownKeyState: "unenrolled" } };
const connected: api.CommunityConnection = { ...disconnected, connected: true,
  identity: { ...disconnected.identity!, lastKnownKeyState: "active" },
  profile: { contributorId: "contributor-001", shortId: "contribu", installationId: "installation-1",
    keyId: disconnected.identity!.keyId, keyState: "active", displayName: "Cedar Frost Grove",
    profileRevision: 1, policyVersion: "test", protocolVersion: 1, allowedOperations: ["identity:read", "identity:rename"]} };
beforeEach(() => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(disconnected);
  vi.mocked(api.fetchCommunityConnection).mockResolvedValue(connected);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("rehydrates a pending handoff without completing until explicit approval", async () => {
  const pending = { configured: true, connected: false, profile: null,
    pending: { stateId: "b".repeat(64), expiresAt: 1900000000, awaitingConfirmation: true as const } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(pending);
  vi.mocked(api.completeCommunityEnrollment).mockResolvedValue({ ...pending, pending: null, connected: true });
  const rendered = render(<CommunitySettings />);
  await screen.findByText(/Pending enrollment reference/);
  expect(api.completeCommunityEnrollment).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  rendered.unmount();
  render(<CommunitySettings />);
  await screen.findByText(/Pending enrollment reference/);
  fireEvent.click(screen.getByRole("button", { name: "Retry confirmation" }));
  expect(api.completeCommunityEnrollment).not.toHaveBeenCalled();
  await screen.findByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: "Confirm connection" }));
  await waitFor(() => expect(api.completeCommunityEnrollment).toHaveBeenCalledWith("b".repeat(64), expect.objectContaining({ signal: expect.any(AbortSignal) })));
});

it("keeps backup access available when community is disabled", async () => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue({ configured: false, connected: false, profile: null, pending: null });
  render(<CommunitySettings />);
  await screen.findByText(/Community service is not configured/);
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Backup and restore" })).toHaveAttribute("href", "#/settings/general/backups");
  expect(api.fetchCommunityConnection).not.toHaveBeenCalled();
});

it("launches only the public hosted URL and rejects a handoff from another service", async () => {
  vi.mocked(api.startCommunityEnrollment).mockResolvedValue({ ...disconnected,
    authorizationUrl: "https://community.test/v1/discord/start?confirmationHash=public-hash",
    pending: { stateId: null, expiresAt: 1900000000, awaitingConfirmation: true } });
  render(<CommunitySettings />);
  fireEvent.click(await screen.findByRole("button", { name: "Connect with Discord" }));
  const link = await screen.findByRole("link", { name: "Continue with Discord" });
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  const input = screen.getByLabelText("Discord confirmation URL or enrollment reference");
  fireEvent.change(input, { target: { value: "https://attacker.test/v1/discord/confirm?state=" + "b".repeat(64) } });
  expect(screen.getByRole("button", { name: "Review connection" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "https://community.test/v1/discord/confirm?state=" + "b".repeat(64) } });
  expect(screen.getByRole("button", { name: "Review connection" })).toBeEnabled();
  fireEvent.change(input, { target: { value: "https://community.test/v1/discord/callback?state=" + "b".repeat(64) + "&code=provider-code" } });
  expect(input).toHaveValue("b".repeat(64));
  expect(document.body.innerHTML).not.toContain("provider-code");
  expect(api.completeCommunityEnrollment).not.toHaveBeenCalled();
});

it.each([
  ["identity_incomplete", "signing identity is incomplete"],
  ["identity_corrupt", "signing identity does not match"],
  ["recovery_required", "requires identity recovery"],
  ["key_inactive", "signing key is no longer active"],
])("blocks enrollment for %s and retains backup access", async (code, message) => {
  vi.mocked(api.fetchLocalCommunityConnection).mockRejectedValue(new api.CommunityApiError(code, false, 409));
  render(<CommunitySettings />);
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Backup and restore" })).toBeVisible();
});

it.each([
  ["membership_ineligible", "membership is no longer eligible"],
  ["membership_unavailable", "membership could not be verified"],
  ["network_unavailable", "service is unreachable"],
])("keeps last profile separate from verified access on %s", async (code, message) => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(connected);
  vi.mocked(api.fetchCommunityConnection).mockRejectedValue(new api.CommunityApiError(code, true, 503));
  render(<CommunitySettings />);
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.getByText(/Last saved profile/)).toHaveTextContent("Cedar Frost Grove");
  expect(screen.getByLabelText("Contributor name")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Connect with Discord" })).not.toBeInTheDocument();
});

it("restores the exact pending rename and renders names as text", async () => {
  const pending = { ...connected, pendingRename: { operation_id: "same-rename", display_name: "<img src=x onerror=alert(1)>" } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(pending);
  vi.mocked(api.fetchCommunityConnection).mockResolvedValue(pending);
  vi.mocked(api.renameCommunityProfile).mockResolvedValue({ ...connected,
    profile: { ...connected.profile!, displayName: pending.pendingRename.display_name, profileRevision: 2 }, pendingRename: null });
  render(<CommunitySettings />);
  const retry = await screen.findByRole("button", { name: "Retry name change" });
  await waitFor(() => expect(retry).toBeEnabled());
  expect(screen.getByLabelText("Contributor name")).toHaveAttribute("readonly");
  fireEvent.click(retry);
  await waitFor(() => expect(api.renameCommunityProfile).toHaveBeenCalledWith(pending.pendingRename.display_name, "same-rename", expect.objectContaining({ signal: expect.any(AbortSignal) })));
  expect(document.querySelector("img[src=x]")).toBeNull();
  expect(screen.getByRole("heading", { name: "Contributor profile" })).toBeVisible();
});

it("re-reads durable enrollment after a lost completion response", async () => {
  const pending = { ...disconnected, pending: { stateId: "b".repeat(64), expiresAt: 1900000000, awaitingConfirmation: true as const } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(pending);
  vi.mocked(api.completeCommunityEnrollment).mockRejectedValue(new api.CommunityApiError("network_unavailable", true, 503));
  render(<CommunitySettings />);
  const retry = await screen.findByRole("button", { name: "Retry confirmation" });
  await waitFor(() => expect(retry).toBeEnabled());
  fireEvent.click(retry);
  fireEvent.click(await screen.findByRole("button", { name: "Confirm connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("service is unreachable");
  expect(screen.getByRole("button", { name: "Retry confirmation" })).toBeEnabled();
  expect(api.fetchLocalCommunityConnection).toHaveBeenCalledTimes(2);
});

it("resumes a persisted authorization and allows explicit cancellation", async () => {
  const pending = { ...disconnected, pending: { stateId: null, expiresAt: 1900000000, awaitingConfirmation: true as const } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(pending);
  vi.mocked(api.startCommunityEnrollment).mockResolvedValue({ ...pending,
    authorizationUrl: "https://community.test/v1/discord/start?confirmationHash=public-hash" });
  vi.mocked(api.cancelCommunityEnrollment).mockResolvedValue(disconnected);
  render(<CommunitySettings />);
  fireEvent.click(await screen.findByRole("button", { name: "Resume Discord authorization" }));
  await screen.findByRole("link", { name: "Continue with Discord" });
  expect(api.completeCommunityEnrollment).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel enrollment" }));
  await screen.findByRole("button", { name: "Connect with Discord" });
  expect(api.cancelCommunityEnrollment).toHaveBeenCalledTimes(1);
});

it("shows an expired handoff without automatically starting or confirming another", async () => {
  const pending = { ...disconnected, pending: { stateId: "b".repeat(64), expiresAt: 1900000000, awaitingConfirmation: true as const } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValueOnce(pending).mockResolvedValue(disconnected);
  vi.mocked(api.completeCommunityEnrollment).mockRejectedValue(new api.CommunityApiError("enrollment_expired", false, 409));
  render(<CommunitySettings />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry confirmation" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("enrollment has expired");
  expect(screen.getByRole("button", { name: "Connect with Discord" })).toBeEnabled();
  expect(api.startCommunityEnrollment).not.toHaveBeenCalled();
  expect(api.completeCommunityEnrollment).toHaveBeenCalledTimes(1);
});

it("accepts only bounded public handoffs from the configured service", () => {
  const state = "b".repeat(64);
  expect(handoffState(state)).toBe(state);
  expect(handoffState(`https://community.test/v1/discord/callback?state=${state}&code=provider-code`, "https://community.test")).toBe(state);
  expect(handoffState(`https://community.test/v1/discord/confirm?state=${state}&state=${state}`, "https://community.test")).toBeNull();
  expect(handoffState(`https://community.test/other?state=${state}`, "https://community.test")).toBeNull();
});

it("requires explicit rotation confirmation and cancellation never prepares a key", async () => {
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(connected);
  vi.mocked(api.rotateCommunityKey).mockResolvedValue({ ...connected,
    rotationCompleted: { operationId: "rotation-1", keyId: "new-key", committedAt: 1700000030000 } });
  render(<CommunitySettings />);
  const trigger = await screen.findByRole("button", { name: "Rotate key" });
  await waitFor(() => expect(trigger).toBeEnabled());
  expect(api.rotateCommunityKey).not.toHaveBeenCalled();
  fireEvent.click(trigger);
  expect(await screen.findByRole("dialog")).toHaveTextContent("Other separately registered installations retain access");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.rotateCommunityKey).not.toHaveBeenCalled();
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "Confirm rotation" }));
  await screen.findByText(/Key rotation activated/);
  expect(api.rotateCommunityKey).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Backup freshness has not been verified/)).toBeVisible();
});

it("reloads pending rotation without sending and retries only the saved operation", async () => {
  const pending = { ...connected, pendingRotation: { operationId: "rotation-1", newKeyId: "new-key", state: "pending" as const } };
  vi.mocked(api.fetchLocalCommunityConnection).mockResolvedValue(pending);
  vi.mocked(api.reconcileCommunityKey).mockResolvedValue({ ...pending, rotationStatus: "not_committed" });
  vi.mocked(api.retryCommunityKey).mockRejectedValue(new api.CommunityApiError("key_inactive", false, 409));
  render(<CommunitySettings />);
  const check = await screen.findByRole("button", { name: "Check rotation" });
  await waitFor(() => expect(check).toBeEnabled());
  expect(api.rotateCommunityKey).not.toHaveBeenCalled();
  expect(api.reconcileCommunityKey).not.toHaveBeenCalled();
  expect(api.fetchCommunityConnection).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Contributor name")).toBeDisabled();
  fireEvent.click(check);
  await screen.findByText(/service has not committed this saved rotation/);
  fireEvent.click(screen.getByRole("button", { name: "Retry saved rotation" }));
  expect(api.retryCommunityKey).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm retry" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("key is no longer active");
  expect(screen.getByRole("button", { name: "Check rotation" })).toBeEnabled();
  expect(screen.getByText("rotation-1")).toBeVisible();
});
