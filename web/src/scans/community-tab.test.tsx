import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { findCommunityVersions, listCommunityMasters } from "../api/api-community-masters";
import { CommunityTab } from "./community-tab";

const can = vi.hoisted(() => ({ search: true }));
vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), listCommunityMasters: vi.fn(), findCommunityVersions: vi.fn(),
}));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(),
  useCan: (capability: string) => capability !== "community.search" || can.search }));
afterEach(() => { cleanup(); vi.resetAllMocks(); can.search = true; });
const target = { source: "jellyfin" as const, item_id: "movie-1", media_source_id: null };

it("finds only community versions from its Find Filters button", async () => {
  vi.mocked(listCommunityMasters).mockResolvedValue({ version: 1, links: [] });
  vi.mocked(findCommunityVersions).mockResolvedValue({ version: 1, findKey: "fk", localVersionId: "mv_1", keysUsed: [], results: [], error: null });
  render(<CommunityTab target={target} />);
  expect(await screen.findByText(/No community filters for this version yet/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Find Filters" }));
  const dialog = screen.getByRole("dialog", { name: "Find Filters" });
  expect(await within(dialog).findByText("No community versions match this file.")).toBeVisible();
  expect(findCommunityVersions).toHaveBeenCalledTimes(1);
  expect(within(dialog).queryByText(/Associate/)).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("explains the tab without community access", () => {
  can.search = false;
  render(<CommunityTab target={target} />);
  expect(screen.getByRole("note")).toHaveTextContent("Your account can't use community filters.");
  expect(screen.queryByRole("button", { name: "Find Filters" })).not.toBeInTheDocument();
  expect(listCommunityMasters).not.toHaveBeenCalled();
});
