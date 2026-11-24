import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../api/api-community-sharing";
import { CommunityApiError } from "../api/api-community";
import { fetchVersionAliases } from "../api/api-community-masters";
import { ShareAction } from "./sharing-dialog";
import { pendingPublication, sharingContextFixture, sharingPreviewFixture } from "./sharing-test-fixtures";

const access = vi.hoisted(() => ({ grants: new Set<string>(), current: true, refresh: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({
  ...await original<typeof import("../auth/auth-gate")>(),
  useCan: (capability: string) => access.grants.has(capability),
  usePrincipal: () => ({ userId: 1, role: "admin", capabilities: access.grants }),
  useOptionalAuthenticationGate: () => ({ isAuthenticationCurrent: () => access.current, refreshAuthenticationStatus: access.refresh }),
}));
vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), fetchVersionAliases: vi.fn() }));
vi.mock("../api/api-community-sharing", async (original) => ({ ...await original<typeof api>(),
  fetchSharingContext: vi.fn(), prepareSharingPreview: vi.fn(), approveSharingPreview: vi.fn(),
  discardSharingPreview: vi.fn(), retryPublication: vi.fn(), checkPublication: vi.fn(), cancelPublication: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [
    { kind: "alias", aliasId: "theatrical", label: "Theatrical" }, { kind: "alias", aliasId: "extended", label: "Extended Edition" },
    { kind: "other", label: "Other" }] });
  access.grants = new Set(["community.publish.other_review", "community.publications.retry.own"]);
  access.current = true;
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), sourceOwnerId: 2 });
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(sharingPreviewFixture());
  vi.mocked(api.discardSharingPreview).mockResolvedValue({ discarded: true });
  vi.mocked(api.approveSharingPreview).mockResolvedValue(pendingPublication);
  vi.mocked(api.retryPublication).mockResolvedValue({ ...pendingPublication, state: "paused" });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function chooseVersion(value = "theatrical") {
  fireEvent.change(await screen.findByRole("combobox", { name: "Which version is this?" }), { target: { value } });
}
async function open() {
  const rendered = render(<ShareAction source={{ kind: "review", id: "other-review" }} name="Shared source" />);
  fireEvent.click(screen.getByRole("button", { name: "Share Shared source" }));
  await waitFor(() => expect(api.fetchSharingContext).toHaveBeenCalledOnce());
  if (access.grants.has("community.publish.other_review")) await chooseVersion();
  return rendered;
}
async function prepare() {
  await screen.findByRole("region", { name: "Filter summary" });
}
function approve() {
  fireEvent.click(screen.getByRole("button", { name: "Share contribution" }));
}
it("does not expose or fetch sharing without a publication capability", () => {
  access.grants.clear();
  render(<ShareAction source={{ kind: "review", id: "other-review" }} name="Hidden source" />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(api.fetchSharingContext).not.toHaveBeenCalled();
});
it("does not let an own-review grant enable publishing another user's review", async () => {
  access.grants = new Set(["community.publish.own_review"]);
  await open();
  await screen.findByRole("button", { name: "Close" });
  expect(screen.queryByRole("button", { name: "Share contribution" })).not.toBeInTheDocument();
  expect(api.prepareSharingPreview).not.toHaveBeenCalled();
});
it("shows the acting contributor in a cross-user preview", async () => {
  const preview = sharingPreviewFixture();
  preview.preview.artifact.author.contributor_id = "acting-admin-contributor";
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(preview);
  await open(); await prepare();
  expect(screen.getByText("Sharing as acting-admin-contributor")).toBeVisible();
  expect(api.approveSharingPreview).not.toHaveBeenCalled();
});
it("allows check-only management without publishing or borrowing own retry/retract grants", async () => {
  access.grants = new Set(["community.publications.check.other", "community.publications.retry.own", "community.publications.retract.own"]);
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), sourceOwnerId: 2,
    jobs: [{ ...pendingPublication, ownerId: 2, contributorId: "original-user-contributor" }] });
  await open();
  fireEvent.click(within(await screen.findByRole("region", { name: "Saved publications" })).getByRole("button"));
  expect(screen.getByText("original-user-contributor")).toBeVisible();
  expect(screen.getByRole("button", { name: "Check outcome" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Retry saved contribution" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel publication" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Share contribution" })).not.toBeInTheDocument();
  expect(api.prepareSharingPreview).not.toHaveBeenCalled();
});
it("queues an approved contribution without sending when retry is not granted", async () => {
  access.grants.delete("community.publications.retry.own");
  await open(); await prepare(); approve();
  await screen.findByRole("region", { name: "Publication status" });
  expect(api.retryPublication).not.toHaveBeenCalled();
});

it.each([false, true])("keeps management independent of viewer enrollment with held=%s", async held => {
  access.grants = new Set(["community.publications.check.other", "community.publications.retry.other"]);
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), sourceOwnerId: 2,
    accessVerified: false, accessError: "not_connected",
    jobs: [{ ...pendingPublication, ownerId: 2, contributorId: "original-user-contributor",
      state: held ? "paused" : "pending", errorCode: held ? "not_connected" : null }] });
  await open();
  fireEvent.click(within(await screen.findByRole("region", { name: "Saved publications" })).getByRole("button"));
  expect(screen.getByText("original-user-contributor")).toBeVisible();
  const retry = screen.getByRole("button", { name: "Retry saved contribution" });
  const check = screen.getByRole("button", { name: "Check outcome" });
  if (held) {
    expect(retry).toBeDisabled();
    expect(check).toBeDisabled();
    expect(screen.getByRole("region", { name: "Publication status" })).toHaveTextContent("held until reconnect");
  } else {
    expect(retry).toBeEnabled();
    expect(check).toBeEnabled();
  }
  expect(api.retryPublication).not.toHaveBeenCalled();
  expect(api.checkPublication).not.toHaveBeenCalled();
});
it("does not retry or discard after the approving authentication lifetime ends", async () => {
  let finish!: (job: api.PublicationJob) => void;
  vi.mocked(api.approveSharingPreview).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await open(); await prepare(); approve();
  access.current = false;
  await act(async () => finish(pendingPublication));
  expect(api.retryPublication).not.toHaveBeenCalled();
  expect(api.discardSharingPreview).not.toHaveBeenCalled();
});
it("lets the connected owner manage a saved historical connection failure", async () => {
  access.grants = new Set(["community.publications.check.own", "community.publications.retry.own"]);
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), sourceOwnerId: 1,
    accessVerified: true, accessError: null,
    jobs: [{ ...pendingPublication, ownerId: 1, state: "paused", errorCode: "not_connected" }] });
  await open();
  fireEvent.click(within(await screen.findByRole("region", { name: "Saved publications" })).getByRole("button"));
  expect(screen.getByRole("button", { name: "Retry saved contribution" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Check outcome" })).toBeEnabled();
  expect(screen.getByRole("region", { name: "Publication status" })).not.toHaveTextContent("held until reconnect");
});
it("cancels a held unsent publication locally without checking or retrying", async () => {
  access.grants = new Set(["community.publications.check.other", "community.publications.retract.other"]);
  const held = { ...pendingPublication, ownerId: 2, state: "paused" as const, errorCode: "not_connected" };
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), sourceOwnerId: 2,
    accessVerified: true, jobs: [held] });
  vi.mocked(api.cancelPublication).mockResolvedValue({ ...held, state: "cancelled", cancelRequested: true, errorCode: null });
  await open();
  fireEvent.click(within(await screen.findByRole("region", { name: "Saved publications" })).getByRole("button"));
  expect(screen.getByRole("button", { name: "Check outcome" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel publication" }));
  await waitFor(() => expect(api.cancelPublication).toHaveBeenCalledOnce());
  expect(await screen.findByRole("heading", { name: "cancelled" })).toBeVisible();
  expect(api.checkPublication).not.toHaveBeenCalled();
  expect(api.retryPublication).not.toHaveBeenCalled();
});
it("aborts a preview on unmount without emitting a late discard", async () => {
  let finish!: (preview: api.SharingPreview) => void;
  vi.mocked(api.prepareSharingPreview).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const rendered = await open();
  await waitFor(() => expect(api.prepareSharingPreview).toHaveBeenCalledOnce());
  const signal = vi.mocked(api.prepareSharingPreview).mock.calls[0][2]!;
  rendered.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish(sharingPreviewFixture()));
  expect(api.discardSharingPreview).not.toHaveBeenCalled();
});
it("refreshes a stale source automatically and waits for another share click", async () => {
  vi.mocked(api.approveSharingPreview).mockRejectedValue(new CommunityApiError("preview_stale", false, 409));
  await open(); await prepare(); approve();
  await waitFor(() => expect(api.fetchSharingContext).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("region", { name: "Filter summary" })).not.toBeInTheDocument();
  await prepare();
  expect(api.prepareSharingPreview).toHaveBeenCalledTimes(2);
  expect(api.approveSharingPreview).toHaveBeenCalledOnce();
  expect(api.retryPublication).not.toHaveBeenCalled();
});
it.each([403, 404])("clears private source and preview state on %s without follow-up sends", async status => {
  vi.mocked(api.approveSharingPreview).mockRejectedValue(new CommunityApiError(status === 403 ? "capability_denied" : "not_found", false, status));
  await open(); await prepare(); approve();
  await waitFor(() => expect(access.refresh).toHaveBeenCalledOnce());
  expect(screen.queryByRole("region", { name: "Filter summary" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit details" })).not.toBeInTheDocument();
  expect(api.fetchSharingContext).toHaveBeenCalledOnce();
  expect(api.retryPublication).not.toHaveBeenCalled();
  expect(api.discardSharingPreview).not.toHaveBeenCalled();
});
