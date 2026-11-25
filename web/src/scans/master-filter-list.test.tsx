import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { MasterApiError, voteMasterFilter } from "../api/api-community-masters";
import type { MasterFilter, MasterPreferences } from "../api/api-community-master-types";
import { masterFilter, masterView } from "./community-master-test-fixtures";
import { MasterFilterList, MasterPreferenceControls } from "./master-filter-list";

vi.mock("../api/api-community-masters", async (original) => ({
  ...await original<typeof import("../api/api-community-masters")>(), voteMasterFilter: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function Harness({ filters, canVote = true }: { filters: MasterFilter[]; canVote?: boolean }) {
  const [preferences, setPreferences] = useState<MasterPreferences>({ confidenceThreshold: 0, showLowConfidence: false });
  const [rows, setRows] = useState(filters);
  return <>
    <MasterPreferenceControls preferences={preferences} onChange={setPreferences} />
    <MasterFilterList view={masterView(rows)} preferences={preferences} canVote={canVote}
      onFilter={(next) => setRows((current) => current.map((row) => row.filterId === next.filterId ? next : row))} />
  </>;
}
const rowNames = () => within(screen.getByRole("table")).queryAllByRole("row").slice(1).map((row) => row.querySelector("td")?.textContent);
const at = (startMs: number) => ({ ...masterFilter().master, startMs, endMs: startMs + 1000 });

it("filters by the confidence threshold and keeps Low Confidence hidden until toggled", () => {
  render(<Harness filters={[
    masterFilter({ filterId: "strong", confidence: 0.6, master: { ...at(1000), confidence: 0.6 } }),
    masterFilter({ filterId: "weak", master: { ...at(3000), confidence: 0.2 } }),
    masterFilter({ filterId: "low", master: { ...at(5000), confidence: 0.12, lowConfidence: true }, locallyVerified: true }),
  ]} />);
  const toggle = screen.getByRole("checkbox", { name: "Show Low Confidence filters" });
  expect(toggle).not.toBeChecked();
  expect(rowNames()).toEqual(["00:01.000–00:02.000", "00:03.000–00:04.000"]);
  expect(screen.getByText(/1 hidden by your filter settings/)).toBeInTheDocument();
  fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), { target: { value: "0.5" } });
  expect(rowNames()).toEqual(["00:01.000–00:02.000"]);
  fireEvent.click(toggle);
  fireEvent.change(screen.getByRole("slider", { name: "Minimum confidence" }), { target: { value: "0.1" } });
  expect(rowNames()).toHaveLength(3);
  expect(screen.getByText(/Low Confidence · 12%/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Locally verified" })).toBeInTheDocument();
});

it("is read-only: no Confirm, Reject or Reset, and explains what the preferences decide", () => {
  render(<Harness filters={[masterFilter()]} />);
  for (const name of [/^Confirm/, /^Reject/, /^Reset/]) expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.getByText(/start disabled when a review includes or refreshes them/)).toBeInTheDocument();
});

it("shows contributor counts without names and explains Low Confidence in a tooltip", () => {
  vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(20, 20, 120, 24));
  render(<Harness filters={[masterFilter({ confidence: 0.5 })]} />);
  fireEvent.click(screen.getByRole("checkbox", { name: "Show Low Confidence filters" }));
  const header = screen.getByRole("columnheader", { name: "Contributors" });
  const column = Array.from(header.parentElement!.children).indexOf(header);
  expect(screen.getAllByRole("row")[1].children[column]).toHaveTextContent(/^4$/);
  vi.restoreAllMocks();
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

it("hides downvoted rows only from the list, keeping one downvoted here until the setting changes", async () => {
  vi.mocked(voteMasterFilter).mockResolvedValue({ version: 1, state: "completed", result: {} });
  render(<Harness filters={[
    masterFilter({ filterId: "kept", master: at(1000) }),
    masterFilter({ filterId: "down", myVote: "down", master: at(5000) }),
  ]} />);
  const toggle = screen.getByRole("checkbox", { name: "Hide downvoted filters" });
  expect(rowNames()).toHaveLength(2);
  fireEvent.click(toggle);
  expect(rowNames()).toEqual(["00:01.000–00:02.000"]);
  expect(screen.getByText(/1 hidden by your filter settings/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Thumbs down/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: /^Thumbs down/ })).toHaveAttribute("aria-pressed", "true"));
  expect(rowNames()).toHaveLength(1);
  fireEvent.click(toggle);
  fireEvent.click(toggle);
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
