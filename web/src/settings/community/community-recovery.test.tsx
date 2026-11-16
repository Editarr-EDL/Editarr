import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../../api/api-community";
import { CommunityIdentityRecovery } from "./community-recovery";

vi.mock("../../auth/auth-gate", async (original) => ({
  ...await original<typeof import("../../auth/auth-gate")>(),
  useCan: () => true,
}));

vi.mock("../../api/api-community", async (original) => ({
  ...await original<typeof api>(), fetchCommunityRecovery: vi.fn(), startCommunityRecovery: vi.fn(),
  completeCommunityRecovery: vi.fn(), checkCommunityRecovery: vi.fn(), retryCommunityRecovery: vi.fn(),
}));
const initial: api.CommunityRecoveryStatus = { configured: true, serviceUrl: "https://community.test",
  identity: { installationId: "00000000-0000-4000-8000-000000000001", keyId: "sha256:" + "a".repeat(64), contributorId: "contributor-001" },
  pending: null, completed: null };
const pending: api.CommunityRecoveryStatus = { ...initial, pending: { operationId: "saved-recovery",
  stateId: "b".repeat(64), oldKeyId: initial.identity!.keyId, newKeyId: "sha256:" + "c".repeat(64) } };
const changed = vi.fn(async () => {});
beforeEach(() => { vi.mocked(api.fetchCommunityRecovery).mockResolvedValue(initial); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("requires confirmation before preparing and sends nothing after cancellation", async () => {
  render(<CommunityIdentityRecovery onChanged={changed} />);
  fireEvent.click(await screen.findByRole("button", { name: "Recover with Discord" }));
  expect(api.startCommunityRecovery).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toHaveTextContent("No key is replaced until your final confirmation");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.startCommunityRecovery).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText(/no account-transfer bypass/)).toBeVisible();
});

it("launches separate recovery authorization and strips provider code before final approval", async () => {
  vi.mocked(api.startCommunityRecovery).mockResolvedValue({ ...pending, pending: { ...pending.pending!, stateId: null },
    authorizationUrl: "https://community.test/v1/discord/recovery-start?confirmationHash=public" });
  render(<CommunityIdentityRecovery onChanged={changed} />);
  fireEvent.click(await screen.findByRole("button", { name: "Recover with Discord" }));
  fireEvent.click(screen.getByRole("button", { name: "Prepare recovery" }));
  expect(await screen.findByRole("link", { name: "Authorize recovery with Discord" })).toHaveAttribute("rel", "noopener noreferrer");
  const input = screen.getByLabelText("Discord recovery URL or reference");
  fireEvent.change(input, { target: { value: "https://other.test/v1/discord/confirm?state=" + "b".repeat(64) } });
  expect(screen.getByRole("button", { name: "Review recovery" })).toBeDisabled();
  fireEvent.change(input, { target: { value: "https://community.test/v1/discord/callback?state=" + "b".repeat(64) + "&code=private-provider-code" } });
  expect(input).toHaveValue("b".repeat(64));
  expect(document.body.innerHTML).not.toContain("private-provider-code");
  fireEvent.click(screen.getByRole("button", { name: "Review recovery" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("Other installations retain access");
  expect(api.completeCommunityRecovery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.completeCommunityRecovery).not.toHaveBeenCalled();
});

it("reload is passive and checking never performs a confirmed retry", async () => {
  vi.mocked(api.fetchCommunityRecovery).mockResolvedValue(pending);
  vi.mocked(api.checkCommunityRecovery).mockResolvedValue({ ...pending, recoveryStatus: "not_committed" });
  render(<CommunityIdentityRecovery onChanged={changed} />);
  fireEvent.click(await screen.findByRole("button", { name: "Check recovery" }));
  await screen.findByText(/service has not committed/);
  expect(api.checkCommunityRecovery).toHaveBeenCalledTimes(1);
  expect(api.startCommunityRecovery).not.toHaveBeenCalled();
  expect(api.completeCommunityRecovery).not.toHaveBeenCalled();
  expect(api.retryCommunityRecovery).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Retry saved recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.retryCommunityRecovery).not.toHaveBeenCalled();
});

it("retains pending state after outage and confirms a retry before the backup reminder", async () => {
  vi.mocked(api.fetchCommunityRecovery).mockResolvedValue(pending);
  vi.mocked(api.retryCommunityRecovery).mockRejectedValueOnce(new api.CommunityApiError("network_unavailable", true, 503))
    .mockResolvedValueOnce({ ...initial, identity: { ...initial.identity!, keyId: pending.pending!.newKeyId }, completed: pending.pending });
  render(<CommunityIdentityRecovery onChanged={changed} />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry saved recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm recovery" }));
  await screen.findByRole("alert");
  expect(screen.getByText("saved-recovery")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry saved recovery" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm recovery" }));
  await waitFor(() => expect(screen.getByText(/Identity recovery activated/)).toBeVisible());
  expect(screen.getByText(/Backup freshness has not been verified/)).toBeVisible();
  expect(screen.getByLabelText("Affected signing key fingerprint")).toHaveValue(pending.pending!.newKeyId);
});