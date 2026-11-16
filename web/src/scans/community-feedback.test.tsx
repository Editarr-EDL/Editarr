import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommunityFeedback } from "./community-feedback";
import { CommunityApiError } from "../api/api-community";
import { readCommunityFeedback, sendCommunityVote, type Feedback } from "../api/api-community-votes";

vi.mock("../api/api-community-votes", () => ({ readCommunityFeedback: vi.fn(), sendCommunityVote: vi.fn() }));
vi.mock("../auth/auth-gate", async (original) => ({ ...await original<typeof import("../auth/auth-gate")>(), useCan: () => true }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
const state = (vote: "like" | "dislike" | "neutral" = "neutral"): Feedback => ({ version: 1, revisionId: "one",
  scope: { serviceUrl: "https://community.example", contributorId: "reader" },
  current: { vote, eligible: true, reason: null }, totals: { likes: 1, dislikes: 0 }, pending: null, error: null });

it("reads private state explicitly and recovers an offline exact intent without fabricating totals", async () => {
  vi.mocked(readCommunityFeedback).mockResolvedValue(state());
  vi.mocked(sendCommunityVote).mockRejectedValueOnce(new CommunityApiError("network_unavailable", true, 0)).mockResolvedValue(undefined);
  render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" />);
  expect(readCommunityFeedback).not.toHaveBeenCalled();
  expect(screen.getByRole("radio", { name: /^Like$/ })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Check feedback" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: "Neutral" })).toBeChecked());
  fireEvent.click(screen.getByRole("radio", { name: /^Like$/ }));
  await screen.findByText(/Pending like change/);
  expect(screen.queryByText(/Current totals:/)).toBeNull();
  const intent = vi.mocked(sendCommunityVote).mock.calls[0][1];
  vi.mocked(readCommunityFeedback).mockResolvedValue(state("dislike"));
  fireEvent.click(screen.getByRole("button", { name: "Retry saved vote" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: /^Dislike$/ })).toBeChecked());
  expect(sendCommunityVote).toHaveBeenLastCalledWith("one", intent, expect.any(AbortSignal), true);
  expect(screen.queryByRole("button", { name: "Retry saved vote" })).toBeNull();
});

it("exposes self/import denial and keeps reports separate from dislikes", async () => {
  vi.mocked(readCommunityFeedback).mockResolvedValue({ ...state(), current: { vote: "neutral", eligible: false, reason: "self_vote" } });
  const { rerender } = render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" />);
  fireEvent.click(screen.getByRole("button", { name: "Check feedback" }));
  await screen.findByText("Authors cannot vote on their own revisions.");
  expect(screen.getByRole("radio", { name: /^Like$/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Report abuse" })).toBeDisabled();
  vi.mocked(readCommunityFeedback).mockResolvedValue({ ...state(), current: { vote: "neutral", eligible: false, reason: "import_required" } });
  rerender(<CommunityFeedback revisionId="two" disabled={false} refreshKey="target" />);
  fireEvent.click(screen.getByRole("button", { name: "Check feedback" }));
  await screen.findByText("An acknowledged import of this exact revision is required.");
  expect(sendCommunityVote).not.toHaveBeenCalled();
});

it("discards old private replies and never carries own votes into another candidate or stale lookup", async () => {
  let resolve!: (value: Feedback) => void;
  vi.mocked(readCommunityFeedback).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const { rerender } = render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" />);
  fireEvent.click(screen.getByRole("button", { name: "Check feedback" }));
  rerender(<CommunityFeedback revisionId="two" disabled={true} refreshKey="new-target" />);
  resolve(state("like"));
  await waitFor(() => expect(screen.getByText("Current vote unavailable.")).toBeVisible());
  expect(screen.getByRole("radio", { name: /^Like$/ })).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Check feedback" })).toBeDisabled();
});

it("replaces private own state on contributor change and identifies authors without a local import", async () => {
  vi.mocked(readCommunityFeedback).mockResolvedValueOnce(state("like")).mockResolvedValueOnce({
    ...state(), scope: { ...state().scope, contributorId: "author" },
    current: { vote: "neutral", eligible: false, reason: "import_required" },
  });
  render(<CommunityFeedback revisionId="one" authorId="author" disabled={false} refreshKey="target" />);
  fireEvent.click(screen.getByRole("button", { name: "Check feedback" }));
  await waitFor(() => expect(screen.getByRole("radio", { name: /^Like$/ })).toBeChecked());
  fireEvent.click(screen.getByRole("button", { name: "Check feedback" }));
  await screen.findByText("Authors cannot vote on their own revisions.");
  expect(screen.getByRole("radio", { name: /^Like$/ })).not.toBeChecked();
  expect(screen.getByRole("radio", { name: "Neutral" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "Neutral" })).toBeDisabled();
});