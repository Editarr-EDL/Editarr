import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as api from "../api/api-community-sharing";
import { CommunityApiError } from "../api/api-community";
import { fetchVersionAliases } from "../api/api-community-masters";
import { ShareAction } from "./sharing-dialog";
import { pendingPublication, sharingContextFixture, sharingPreviewFixture } from "./sharing-test-fixtures";

vi.mock("../auth/auth-gate", async (original) => {
  const { ownerPrincipal } = await import("../settings/users/users.test-support");
  return { ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true, usePrincipal: () => ownerPrincipal };
});
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
  vi.mocked(api.fetchSharingContext).mockResolvedValue(sharingContextFixture());
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(sharingPreviewFixture());
  vi.mocked(api.discardSharingPreview).mockResolvedValue({ discarded: true });
  vi.mocked(api.approveSharingPreview).mockResolvedValue(pendingPublication);
  vi.mocked(api.retryPublication).mockResolvedValue({ ...pendingPublication, state: "paused", errorCode: "network_unavailable" });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
async function chooseVersion(value = "theatrical") {
  fireEvent.change(await screen.findByRole("combobox", { name: "Which version is this?" }), { target: { value } });
}
async function open(kind: "scan" | "review" = "scan") {
  render(<ShareAction source={{ kind, id: "source-local" }} name="Public source" />);
  fireEvent.click(screen.getByRole("button", { name: "Share Public source" }));
  await screen.findByRole("button", { name: "Share contribution" });
  await chooseVersion();
}
async function prepare() {
  return screen.findByRole("region", { name: "Filter summary" });
}
it("points to the instance operator when sharing limits are missing", async () => {
  vi.mocked(api.fetchSharingContext).mockRejectedValue(new CommunityApiError("preview_not_configured", false, 503));
  render(<ShareAction source={{ kind: "scan", id: "source-local" }} name="Public source" />);
  fireEvent.click(screen.getByRole("button", { name: "Share Public source" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ask your Editarr instance operator to configure sharing limits.");
  expect(screen.queryByRole("link", { name: "Community connection settings" })).not.toBeInTheDocument();
});
it("explains what audio filters share and what a missing fingerprint means", async () => {
  const value = sharingPreviewFixture();
  vi.mocked(api.prepareSharingPreview).mockResolvedValue({
    ...value, preview: { ...value.preview, warnings: ["audio_fingerprint_unsupported"] },
  });
  await open();
  const preview = within(await prepare());
  expect(preview.getByText(/View detected words in the local scan/)).toBeVisible();
  expect(preview.getByRole("list", { name: "Sharing warnings" })).toHaveTextContent(
    "Audio fingerprint unavailable for this track. Recipients must verify the audio track and timing.",
  );
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Generate preview" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeEnabled();
});
it("shows total, scanned, added and edited counts for each category and its subcategories", async () => {
  const preview = sharingPreviewFixture("tracked");
  const artifact = preview.preview.artifact;
  artifact.findings.push({ ...artifact.findings[0], event_id: "added-and-edited", subcategory: "vulgarity",
    manual: { added: true, edited: true }, note: "Public wording" });
  artifact.findings.push({ ...artifact.findings[0], event_id: "unknown", category: "violence", subcategory: "violence",
    manual: { added: null, edited: false } });
  preview.preview.manifest.summary.total = artifact.findings.length;
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(preview);
  await open("review");
  const summary = within(await prepare());
  const language = summary.getByRole("button", { name: "Language" });
  expect(language).toHaveAttribute("aria-expanded", "false");
  const cells = (row: HTMLElement) => within(row).getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells(language.closest("tr")!)).toEqual(["2", "1", "1", "2"]);
  expect(summary.queryByRole("rowheader", { name: /Vulgarity/i })).not.toBeInTheDocument();
  fireEvent.click(language);
  expect(language).toHaveAttribute("aria-expanded", "true");
  expect(cells(summary.getByRole("rowheader", { name: /Vulgarity/i }).closest("tr")!)).toEqual(["1", "0", "1", "1"]);
  expect(cells(summary.getByRole("rowheader", { name: /Swearing/i }).closest("tr")!)).toEqual(["1", "1", "0", "1"]);
  expect(cells(summary.getByRole("button", { name: "Violence" }).closest("tr")!)).toEqual(["1", "—Unknown", "—Unknown", "0"]);
  expect(summary.getByText(/A dash indicates an unknown count/)).toBeVisible();
  expect(summary.queryByText("added-and-edited")).not.toBeInTheDocument();
  expect(summary.getByText("Public notes (1)")).toBeVisible();
});
it("loads a summary without publishing and replaces the preview when its input changes", async () => {
  await open(); await prepare();
  expect(api.approveSharingPreview).not.toHaveBeenCalled(); expect(api.retryPublication).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
  expect(api.prepareSharingPreview).toHaveBeenCalledOnce();
  const refreshed = sharingPreviewFixture();
  refreshed.previewId = "preview-edited";
  refreshed.preview.artifact.name = "New public name";
  vi.mocked(api.prepareSharingPreview).mockResolvedValueOnce(refreshed);
  fireEvent.change(screen.getByLabelText("Contribution name"), { target: { value: "New public name" } });
  expect(screen.queryByRole("region", { name: "Filter summary" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeDisabled();
  await prepare();
  expect(api.discardSharingPreview).toHaveBeenCalledWith("preview-ai", expect.any(AbortSignal));
  expect(document.body.textContent).not.toContain("PRIVATE FULL TRANSCRIPT");
  expect(document.body.textContent).not.toContain("/private/media.mp4");
  fireEvent.click(screen.getByRole("button", { name: "Share contribution" }));
  await waitFor(() => expect(api.approveSharingPreview).toHaveBeenCalledExactlyOnceWith("preview-edited", expect.any(AbortSignal)));
});
it("discards a response for an older draft and enables sharing only for the latest summary", async () => {
  let finish!: (value: api.SharingPreview) => void;
  vi.mocked(api.prepareSharingPreview).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await open();
  await waitFor(() => expect(api.prepareSharingPreview).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
  fireEvent.change(screen.getByLabelText("Contribution name"), { target: { value: "Updated draft" } });
  const late = { ...sharingPreviewFixture(), previewId: "stale-id" };
  await act(async () => finish(late));
  expect(screen.queryByRole("region", { name: "Filter summary" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeDisabled();
  await prepare();
  expect(api.discardSharingPreview).toHaveBeenCalledExactlyOnceWith("stale-id", expect.any(AbortSignal));
  expect(api.approveSharingPreview).not.toHaveBeenCalled();
});
it("queues only the exact approved ID, retries the saved job and cancels without deleting remotely", async () => {
  vi.mocked(api.cancelPublication).mockResolvedValue({ ...pendingPublication, state: "cancel_pending", cancelRequested: true });
  await open(); await prepare();
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
  fireEvent.click(screen.getByRole("button", { name: "Share contribution" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The source changed");
  expect(screen.queryByRole("region", { name: "Filter summary" })).not.toBeInTheDocument();
  expect(api.retryPublication).not.toHaveBeenCalled();
});
it("shows actual mixed tracks, public notes and exclusions without saving the review", async () => {
  const context = sharingContextFixture("derived");
  context.audioSources.push({ scanId: "scan-b", track: { ...context.audioSources[0].track!, stream_index: 2, language: "fr", title: "French actually analyzed" } });
  vi.mocked(api.fetchSharingContext).mockResolvedValue(context);
  vi.mocked(api.prepareSharingPreview).mockResolvedValue(sharingPreviewFixture("derived"));
  await open("review");
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeDisabled();
  expect(api.prepareSharingPreview).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Contribution audio source"), { target: { value: "scan-a" } });
  await prepare();
  expect(screen.getByRole("list", { name: "Excluded filters" })).toHaveTextContent("Audio from another track excluded from 1 filter");
  expect(api.prepareSharingPreview).toHaveBeenCalledWith({ kind: "review", id: "source-local" }, expect.objectContaining({ audio_source_scan_id: "scan-a" }), expect.any(AbortSignal));
  fireEvent.change(screen.getByLabelText("Contribution audio source"), { target: { value: "scan-b" } });
  expect(screen.queryByRole("region", { name: "Filter summary" })).not.toBeInTheDocument();
  await prepare();
  expect(api.prepareSharingPreview).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({
    audio_source_scan_id: "scan-b",
  }), expect.any(AbortSignal));
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
it("requires a version before previewing and shows master-set estimates in the summary", async () => {
  vi.mocked(api.prepareSharingPreview).mockResolvedValue({ ...sharingPreviewFixture(),
    estimates: { approximate: true, newFilters: 4, confirmations: 27, adjustments: 3, disputes: 1 },
    versionChoice: { choice: "other", text: "IMAX", label: "IMAX" },
    work: { kind: "movie", tmdb: "603", tvdb: null, title: "The Matrix", year: 1999 }, entryCount: 35 });
  render(<ShareAction source={{ kind: "scan", id: "source-local" }} name="Public source" />);
  fireEvent.click(screen.getByRole("button", { name: "Share Public source" }));
  const picker = await screen.findByRole("combobox", { name: "Which version is this?" });
  expect(Array.from((picker as HTMLSelectElement).options).map((option) => option.text))
    .toEqual(["Choose a version", "Theatrical", "Extended Edition", "Other"]);
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(api.prepareSharingPreview).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Share contribution" })).toBeDisabled();
  fireEvent.change(picker, { target: { value: "__other__" } });
  fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "IMAX" } });
  const summary = within(await prepare());
  expect(summary.getByText("≈4 new, ≈27 confirmations, 3 adjustments, 1 dispute")).toBeVisible();
  expect(summary.getByText(/The Matrix \(1999\)/)).toHaveTextContent("The Matrix (1999) · IMAX");
  expect(vi.mocked(api.prepareSharingPreview).mock.calls.at(-1)?.[1]).toMatchObject({
    link_id: null, version_choice: { choice: "other", text: "IMAX" } });
  expect(screen.getAllByRole("button", { name: /^Share/ }).filter((button) => button.textContent === "Share")).toHaveLength(1);
});
