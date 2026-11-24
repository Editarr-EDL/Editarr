import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { MasterApiError, voteMasterFilter } from "../api/api-community-masters";
import type { MasterFilter, MasterPreferences } from "../api/api-community-master-types";
import { masterFilter, masterView } from "./community-master-test-fixtures";
import { MasterFilterList, MasterPreferenceControls } from "./master-filter-list";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), voteMasterFilter: vi.fn(), decideMasterFilter: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function Harness({ filters, canVote = true }: { filters: MasterFilter[]; canVote?: boolean }) {
  const [preferences, setPreferences] = useState<MasterPreferences>({ confidenceThreshold: 0, showLowConfidence: false });
  const [rows, setRows] = useState(filters);
  return <>
    <MasterPreferenceControls preferences={preferences} onChange={setPreferences} />
    <MasterFilterList view={masterView(rows)} preferences={preferences} canVote={canVote} canDecide
      onFilter={(next) => setRows((current) => current.map((row) => row.filterId === next.filterId ? next : row))} />
  </>;
}
const rowNames = () => within(screen.getByRole("table")).queryAllByRole("row").slice(1).map((row) => row.querySelector("td")?.textContent);

it("filters by the confidence threshold and keeps Low Confidence hidden until toggled", () => {
  render(<Harness filters={[
    masterFilter({ filterId: "strong", confidence: 0.6, effective: { ...masterFilter().effective, startMs: 1000, endMs: 2000 } }),
    masterFilter({ filterId: "weak", confidence: 0.2, effective: { ...masterFilter().effective, startMs: 3000, endMs: 4000 } }),
    masterFilter({ filterId: "low", confidence: 0.12, lowConfidence: true, effective: { ...masterFilter().effective, startMs: 5000, endMs: 6000 } }),
    masterFilter({ filterId: "mine", confidence: 0.1, touched: true, state: "verified", locallyVerified: true,
      effective: { ...masterFilter().effective, startMs: 7000, endMs: 8000 } }),
  ]} />);
  const toggle = screen.getByRole("checkbox", { name: "Show Low Confidence filters" });
  expect(toggle).not.toBeChecked();
  expect(rowNames()).toEqual(["00:01.000–00:02.000", "00:03.000–00:04.000", "00:07.000–00:08.000"]);
  expect(screen.getByText(/1 hidden by your confidence settings/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), { target: { value: "0.5" } });
  expect(rowNames()).toEqual(["00:01.000–00:02.000", "00:07.000–00:08.000"]);
  fireEvent.click(toggle);
  fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), { target: { value: "0.1" } });
  expect(rowNames()).toHaveLength(4);
  expect(screen.getByText(/Low Confidence · 12%/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Locally verified" })).toBeInTheDocument();
});

it("shows contributor counts without names and explains Low Confidence in a tooltip", () => {
  vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(20, 20, 120, 24));
  render(<Harness filters={[masterFilter({ lowConfidence: true, touched: true, state: "verified" })]} />);
  const header = screen.getByRole("columnheader", { name: "Contributors" });
  const column = Array.from(header.parentElement!.children).indexOf(header);
  expect(screen.getAllByRole("row")[1].children[column]).toHaveTextContent(/^4$/);
  const about = screen.getByRole("button", { name: "About Low Confidence" });
  fireEvent.focus(about);
  expect(screen.getByRole("tooltip")).toHaveTextContent("More people disputed, voted down or didn't find this filter");
  vi.restoreAllMocks();
});

it("uses the server's active flag when preferences are saved and recomputes it for unsaved ones", () => {
  const view = masterView([masterFilter({ active: false, confidence: 0.6 })]);
  const preferences = { confidenceThreshold: 0, showLowConfidence: false };
  const { rerender } = render(<MasterFilterList view={view} preferences={preferences} serverActive canVote={false} canDecide={false} onFilter={vi.fn()} />);
  expect(screen.getByText("Not applied")).toBeInTheDocument();
  rerender(<MasterFilterList view={view} preferences={preferences} canVote={false} canDecide={false} onFilter={vi.fn()} />);
  expect(screen.queryByText("Not applied")).toBeNull();
});

it("votes, clears the vote on a second press and retries a retryable failure with the same operation", async () => {
  vi.mocked(voteMasterFilter).mockResolvedValueOnce({ version: 1, state: "completed", result: {} })
    .mockResolvedValueOnce({ version: 1, state: "completed", result: {} })
    .mockRejectedValueOnce(new MasterApiError("service_unavailable", true, 503))
    .mockResolvedValueOnce({ version: 1, state: "completed", result: {} });
  render(<Harness filters={[masterFilter()]} />);
  const up = () => screen.getByRole("button", { name: /^Thumbs up/ });
  const down = () => screen.getByRole("button", { name: /^Thumbs down/ });
  fireEvent.click(up());
  await waitFor(() => expect(up()).toHaveAttribute("aria-pressed", "true"));
  expect(voteMasterFilter).toHaveBeenLastCalledWith("link-1", "f1", expect.objectContaining({ vote: "up" }), false);
  fireEvent.click(up());
  await waitFor(() => expect(up()).toHaveAttribute("aria-pressed", "false"));
  expect(voteMasterFilter).toHaveBeenLastCalledWith("link-1", "f1", expect.objectContaining({ vote: "none" }), false);
  fireEvent.click(down());
  const retry = await screen.findByRole("button", { name: "Retry vote" });
  const intent = vi.mocked(voteMasterFilter).mock.calls[2][2];
  fireEvent.click(retry);
  await waitFor(() => expect(down()).toHaveAttribute("aria-pressed", "true"));
  expect(voteMasterFilter).toHaveBeenLastCalledWith("link-1", "f1", intent, true);
});

it("explains an ineligible vote", async () => {
  vi.mocked(voteMasterFilter).mockRejectedValue(new MasterApiError("vote_ineligible", false, 409, "self_vote"));
  render(<Harness filters={[masterFilter()]} />);
  fireEvent.click(screen.getByRole("button", { name: /^Thumbs up/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("You can’t vote on your own filter.");
  expect(screen.queryByRole("button", { name: "Retry vote" })).toBeNull();
});
