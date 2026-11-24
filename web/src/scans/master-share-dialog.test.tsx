import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchVersionAliases } from "../api/api-community-masters";
import { fetchSharingContext } from "../api/api-community-sharing";
import { approveMasterSharingPreview, prepareMasterSharingPreview } from "../api/api-community-updates";
import { MasterShareDialog } from "./master-share-dialog";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), fetchVersionAliases: vi.fn() }));
vi.mock("../api/api-community-sharing", () => ({ fetchSharingContext: vi.fn(), discardSharingPreview: vi.fn(async () => ({ discarded: true })) }));
vi.mock("../api/api-community-updates", () => ({ prepareMasterSharingPreview: vi.fn(), approveMasterSharingPreview: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
beforeEach(() => {
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [
    { kind: "alias", aliasId: "theatrical", label: "Theatrical" }, { kind: "alias", aliasId: "directors_cut", label: "Director's Cut" },
    { kind: "other", label: "Other" }] });
  vi.mocked(fetchSharingContext).mockResolvedValue({ sourceOwnerId: 1, name: "Scan", mediaIdentity: null, durationSeconds: 100,
    audioSources: [], sourceTerms: [], jobs: [], terms: { version: "1", text: "Terms" }, accessVerified: true, accessError: null });
});

it("requires a version, sends Other text and renders approximate estimates before sharing", async () => {
  vi.mocked(prepareMasterSharingPreview).mockResolvedValue({ version: 1, previewId: "p1", expiresAt: 1,
    estimates: { approximate: true, newFilters: 4, confirmations: 27, adjustments: 3, disputes: 1 },
    versionChoice: { choice: "other", text: "IMAX", label: "IMAX" },
    work: { kind: "movie", tmdb: "603", tvdb: null, title: "The Matrix", year: 1999 }, entryCount: 35, warnings: [] });
  vi.mocked(approveMasterSharingPreview).mockResolvedValue({} as never);
  render(<MasterShareDialog source={{ kind: "scan", id: "scan-1" }} onClose={vi.fn()} />);
  const select = await screen.findByRole("combobox", { name: "Which version is this?" });
  expect(screen.getByRole("button", { name: "Preview summary" })).toBeDisabled();
  fireEvent.change(select, { target: { value: "__other__" } });
  fireEvent.change(screen.getByLabelText("Version name (optional)"), { target: { value: "IMAX" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview summary" }));
  expect(await screen.findByText("≈4 new, ≈27 confirmations, 3 adjustments, 1 dispute")).toBeInTheDocument();
  expect(prepareMasterSharingPreview).toHaveBeenCalledWith({ kind: "scan", id: "scan-1" }, { identity_confirmed: true, media: null,
    audio_source_scan_id: null, link_id: null, version_choice: { choice: "other", text: "IMAX" } });
  expect(screen.getByText("The Matrix (1999) · IMAX")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Share" }));
  await waitFor(() => expect(approveMasterSharingPreview).toHaveBeenCalledWith("p1"));
  expect(await screen.findByText("Queued for sharing")).toBeInTheDocument();
});

it("explains a missing work identity", async () => {
  const { MasterApiError } = await import("../api/api-community-masters");
  vi.mocked(prepareMasterSharingPreview).mockRejectedValue(new MasterApiError("work_identity_required", false, 409));
  render(<MasterShareDialog source={{ kind: "review", id: "r1" }} onClose={vi.fn()} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Which version is this?" }), { target: { value: "theatrical" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview summary" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Add a TMDB or TVDB ID");
});
