import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../api/api-community-sharing";
import { CommunityApiError } from "../api/api-community";
import { ShareAction } from "./sharing-dialog";
import { pendingPublication, sharingContextFixture, sharingPreviewFixture } from "./sharing-test-fixtures";

vi.mock("../auth/auth-gate", async (original) => {
  const { ownerPrincipal } = await import("../settings/users/users.test-support");
  return { ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true, usePrincipal: () => ownerPrincipal };
});
vi.mock("../api/api-community-sharing", async (original) => ({ ...await original<typeof api>(),
  fetchSharingContext: vi.fn(), prepareSharingPreview: vi.fn(), approveSharingPreview: vi.fn(),
  discardSharingPreview: vi.fn(), retryPublication: vi.fn(), checkPublication: vi.fn(), cancelPublication: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(api.fetchSharingContext).mockResolvedValue(sharingContextFixture());
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(sharingPreviewFixture());
  vi.mocked(api.discardSharingPreview).mockResolvedValue({ discarded: true });
  vi.mocked(api.approveSharingPreview).mockResolvedValue(pendingPublication);
  vi.mocked(api.retryPublication).mockResolvedValue({ ...pendingPublication, state: "paused", errorCode: "network_unavailable" });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function open(kind: "scan" | "review" = "scan") {
  render(<ShareAction source={{ kind, id: "source-local" }} name="Public source" />);
  fireEvent.click(screen.getByRole("button", { name: "Share Public source" }));
  await screen.findByLabelText("I confirm this TMDB/TVDB association.");
}
async function prepare() {
  fireEvent.click(screen.getByLabelText("I confirm this TMDB/TVDB association."));
  fireEvent.click(screen.getByRole("button", { name: "Generate preview" }));
  await screen.findByRole("region", { name: "Outgoing contribution" });
}
it("opens and previews without approving or sending, then invalidates exact consent on changes", async () => {
  await open(); await prepare();
  expect(api.approveSharingPreview).not.toHaveBeenCalled(); expect(api.retryPublication).not.toHaveBeenCalled();
  const confirm = screen.getByLabelText("I may share this data and its notes, and I approve this exact preview.");
  fireEvent.click(confirm);
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Contribution name"), { target: { value: "New public name" } });
  expect(screen.queryByRole("region", { name: "Outgoing contribution" })).not.toBeInTheDocument();
  expect(api.discardSharingPreview).toHaveBeenCalledWith("preview-ai", expect.any(AbortSignal));
  expect(document.body.textContent).not.toContain("PRIVATE FULL TRANSCRIPT");
  expect(document.body.textContent).not.toContain("/private/media.mp4");
});
it("queues only the exact approved ID, retries the saved job and cancels without deleting remotely", async () => {
  vi.mocked(api.cancelPublication).mockResolvedValue({ ...pendingPublication, state: "cancel_pending", cancelRequested: true });
  await open(); await prepare();
  fireEvent.click(screen.getByLabelText("I may share this data and its notes, and I approve this exact preview."));
  fireEvent.click(screen.getByRole("button", { name: "Share contribution" }));
  await screen.findByRole("button", { name: "Retry saved contribution" });
  expect(api.approveSharingPreview).toHaveBeenCalledWith("preview-ai", expect.any(AbortSignal));
  expect(api.retryPublication).toHaveBeenCalledWith("publish-original", expect.any(AbortSignal));
  fireEvent.click(screen.getByRole("button", { name: "Retry saved contribution" }));
  await waitFor(() => expect(api.retryPublication).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Cancel publication" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Cancel publication" }));
  await screen.findByText(/Cancellation does not delete/);
  expect(api.cancelPublication).toHaveBeenCalledWith("publish-original", expect.any(AbortSignal));
  expect(api.prepareSharingPreview).toHaveBeenCalledTimes(1);
});
it("recovers saved publication status on reopen without auto-send", async () => {
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), jobs: [pendingPublication] });
  vi.mocked(api.checkPublication).mockResolvedValue({ ...pendingPublication, state: "published", result: { committedAt: 123, revisionId: pendingPublication.revisionId } });
  await open();
  fireEvent.click(within(screen.getByRole("region", { name: "Saved publications" })).getByRole("button"));
  expect(api.retryPublication).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check outcome" }));
  await screen.findByRole("heading", { name: "Published" });
  expect(screen.getByRole("button", { name: "Cancel publication" })).toBeDisabled();
});
it("clears a revised source's preview and gives an actionable error", async () => {
  vi.mocked(api.approveSharingPreview).mockRejectedValue(new CommunityApiError("preview_stale", false, 409));
  await open(); await prepare();
  fireEvent.click(screen.getByLabelText("I may share this data and its notes, and I approve this exact preview."));
  fireEvent.click(screen.getByRole("button", { name: "Share contribution" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The source changed");
  expect(screen.queryByRole("region", { name: "Outgoing contribution" })).not.toBeInTheDocument();
  expect(api.retryPublication).not.toHaveBeenCalled();
});
it("shows actual mixed tracks, public notes and exclusions without saving the review", async () => {
  const context = sharingContextFixture("derived");
  context.audioSources.push({ scanId: "scan-b", track: { ...context.audioSources[0].track!, stream_index: 2, language: "fr", title: "French actually analyzed" } });
  vi.mocked(api.fetchSharingContext).mockResolvedValue(context);
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(sharingPreviewFixture("derived"));
  await open("review");
  fireEvent.click(screen.getByLabelText("I confirm this TMDB/TVDB association."));
  expect(screen.getByRole("button", { name: "Generate preview" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Contribution audio source"), { target: { value: "scan-a" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate preview" }));
  await screen.findByRole("heading", { name: "Excluded filters" });
  expect(screen.getByText(/event-2.*other audio track/)).toBeInTheDocument();
  expect(api.prepareSharingPreview).toHaveBeenCalledWith({ kind: "review", id: "source-local" }, expect.objectContaining({ audio_source_scan_id: "scan-a" }), expect.any(AbortSignal));
  fireEvent.change(screen.getByLabelText("Contribution audio source"), { target: { value: "scan-b" } });
  expect(screen.queryByRole("region", { name: "Outgoing contribution" })).not.toBeInTheDocument();
});
it.each(["manual", "legacy"])("keeps %s unknown tracks explicitly review-required", async (kind) => {
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(kind), audioSources: [{ scanId: "scan-a", track: null }] });
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(sharingPreviewFixture(kind));
  await open(); await prepare();
  expect(screen.getByText("Audio track unknown. Review required.")).toBeInTheDocument();
  expect(screen.queryByText("Strongly compatible")).not.toBeInTheDocument();
});
it("accepts a typed episode URL when the media has no file-identity proof", async () => {
  vi.mocked(api.fetchSharingContext).mockResolvedValue({ ...sharingContextFixture(), mediaIdentity: null });
  await open();
  fireEvent.change(screen.getByLabelText("Entity"), { target: { value: "episode" } });
  fireEvent.change(screen.getByLabelText("TMDB/TVDB ID or URL"), { target: { value: "https://www.themoviedb.org/tv/12/season/0/episode/2" } });
  fireEvent.change(screen.getByLabelText("Series ID or URL"), { target: { value: "12" } });
  fireEvent.change(screen.getByLabelText("Aired season"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("Aired episode"), { target: { value: "2" } });
  await prepare();
  expect(api.prepareSharingPreview).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ media: {
    provider: "tmdb", entity_type: "episode", reference: "https://www.themoviedb.org/tv/12/season/0/episode/2", series_reference: "12",
    episode_numbers: [{ scheme: "aired", season: 0, number: 2 }],
  } }), expect.any(AbortSignal));
});

it.each([false, true])("allows in-flight cancellation without overriding a committed result (late cancel: %s)", async (lateCancel) => {
  let finish!: (job: api.PublicationJob) => void;
  let finishCancel!: (job: api.PublicationJob) => void;
  vi.mocked(api.retryPublication).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  vi.mocked(api.cancelPublication).mockImplementation(() => new Promise((resolve) => { finishCancel = resolve; }));
  await open(); await prepare();
  fireEvent.click(screen.getByLabelText("I may share this data and its notes, and I approve this exact preview."));
  fireEvent.click(screen.getByRole("button", { name: "Share contribution" }));
  const cancel = await screen.findByRole("button", { name: "Cancel publication" });
  expect(cancel).toBeEnabled();
  fireEvent.click(cancel);
  const cancelled = { ...pendingPublication, state: "cancel_pending" as const, cancelRequested: true };
  if (!lateCancel) {
    finishCancel(cancelled);
    await screen.findByText(/Cancellation does not delete/);
  }
  finish({ ...pendingPublication, state: "published", result: { committedAt: 123, revisionId: pendingPublication.revisionId } });
  await screen.findByRole("heading", { name: "Published" });
  if (lateCancel) {
    finishCancel(cancelled);
    await waitFor(() => expect(screen.queryByText("Requesting local cancellation...")).not.toBeInTheDocument());
  }
  expect(screen.getByRole("button", { name: "Cancel publication" })).toBeDisabled();
});