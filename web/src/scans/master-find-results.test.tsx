import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { applyCommunityMaster, fetchVersionAliases, findCommunityVersions } from "../api/api-community-masters";
import type { FindResult } from "../api/api-community-master-types";
import { masterLink } from "./community-master-test-fixtures";
import { MasterFindResults } from "./master-find-results";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(),
  fetchVersionAliases: vi.fn(), findCommunityVersions: vi.fn(), applyCommunityMaster: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const target = { source: "jellyfin" as const, item_id: "movie-1", media_source_id: null };
const result = (overrides: Partial<FindResult>): FindResult => ({ communityVersionId: "v1", label: "Theatrical", aliasId: "theatrical",
  status: "confirmed", unconfirmed: false, tier: "2", matchPercent: 80, runtimeAgreement: "same_cut", filterCount: 34,
  contributorCount: 5, trust: 0.4, revision: 12, alignmentRequired: false, linkedLinkId: null, ...overrides });

it("shows version cards and confirms before replacing the linked version", async () => {
  vi.mocked(fetchVersionAliases).mockResolvedValue({ version: 1, choices: [{ kind: "alias", aliasId: "theatrical", label: "Theatrical" }, { kind: "other", label: "Other" }] });
  vi.mocked(findCommunityVersions).mockResolvedValue({ version: 1, findKey: "fk", localVersionId: "mv", keysUsed: [], error: null, results: [
    result({ linkedLinkId: "link-1" }),
    result({ communityVersionId: "v2", label: "Extended", unconfirmed: true, status: "candidate", tier: "4", matchPercent: 30,
      alignmentRequired: true, filterCount: 1, contributorCount: 1 }) ] });
  vi.mocked(applyCommunityMaster).mockResolvedValue({ version: 1, link: masterLink({ linkId: "link-2", alignmentState: "required" }) });
  const changed = vi.fn();
  window.addEventListener("editarr:community-masters-changed", changed);
  render(<MasterFindResults target={target} />);
  await screen.findByRole("combobox", { name: "Your version" });
  fireEvent.click(screen.getByRole("button", { name: "Find community versions" }));
  expect(await screen.findByText("30% match")).toBeInTheDocument();
  expect(findCommunityVersions).toHaveBeenCalledWith(target, { alias_id: "theatrical" }, false);
  expect(screen.getByText("In use for this file")).toBeInTheDocument();
  expect(screen.getByText("1 filter · 1 contributor")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Unconfirmed" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Use Extended filters" }));
  expect(applyCommunityMaster).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Replace" }));
  await waitFor(() => expect(applyCommunityMaster).toHaveBeenCalledWith({ target, findKey: "fk", communityVersionId: "v2", replace: true }));
  expect(await screen.findByText(/Align them on the item page/)).toBeInTheDocument();
  expect(changed).toHaveBeenCalled();
  window.removeEventListener("editarr:community-masters-changed", changed);
});
