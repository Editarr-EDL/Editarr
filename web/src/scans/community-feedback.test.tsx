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
  current: { vote, eligible: true, reason: null },
  totals: { likes: 13 + Number(vote === "like"), dislikes: 2 + Number(vote === "dislike") }, pending: null, error: null });
const up = () => screen.getByRole("button", { name: /^Thumbs up/ });
const down = () => screen.getByRole("button", { name: /^Thumbs down/ });

it("fetches feedback on click and toggles an acknowledged vote back to neutral using fresh counts", async () => {
  vi.mocked(readCommunityFeedback).mockResolvedValueOnce(state()).mockResolvedValueOnce(state("like"))
    .mockResolvedValueOnce(state("like")).mockResolvedValueOnce(state());
  vi.mocked(sendCommunityVote).mockResolvedValue(undefined);
  render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" totals={{ likes: 12, dislikes: 2 }} />);
  expect(readCommunityFeedback).not.toHaveBeenCalled();
  expect(up()).toHaveTextContent("(12)");
  expect(down()).toHaveTextContent("(2)");
  fireEvent.click(up());
  await waitFor(() => expect(up()).toHaveTextContent("(14)"));
  expect(up()).toHaveAttribute("aria-pressed", "true");
  expect(sendCommunityVote).toHaveBeenLastCalledWith("one", expect.objectContaining({ vote: "like" }), expect.any(AbortSignal), false);
  fireEvent.click(up());
  await waitFor(() => expect(up()).toHaveTextContent("(13)"));
  expect(up()).toHaveAttribute("aria-pressed", "false");
  expect(sendCommunityVote).toHaveBeenLastCalledWith("one", expect.objectContaining({ vote: "neutral" }), expect.any(AbortSignal), false);
});

it("recovers an uncertain intent using its original operation ID without inventing counts", async () => {
  vi.mocked(readCommunityFeedback).mockResolvedValue(state());
  vi.mocked(sendCommunityVote).mockRejectedValueOnce(new CommunityApiError("network_unavailable", true, 0)).mockResolvedValue(undefined);
  render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" totals={{ likes: 13, dislikes: 2 }} />);
  fireEvent.click(down());
  await screen.findByText(/Pending dislike change/);
  await waitFor(() => expect(screen.getByRole("button", { name: "Retry saved vote" })).toBeEnabled());
  expect(down()).toHaveTextContent("(2)");
  expect(down()).toHaveAttribute("aria-pressed", "false");
  const intent = vi.mocked(sendCommunityVote).mock.calls[0][1];
  vi.mocked(readCommunityFeedback).mockResolvedValue(state("dislike"));
  fireEvent.click(screen.getByRole("button", { name: "Retry saved vote" }));
  await waitFor(() => expect(down()).toHaveAttribute("aria-pressed", "true"));
  expect(sendCommunityVote).toHaveBeenLastCalledWith("one", intent, expect.any(AbortSignal), true);
  expect(screen.queryByRole("button", { name: "Retry saved vote" })).toBeNull();
});

it("shows server eligibility denials and does not send a vote for authors or non-importers", async () => {
  vi.mocked(readCommunityFeedback).mockResolvedValue({ ...state(), current: { vote: "neutral", eligible: false, reason: "self_vote" } });
  const { rerender } = render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" />);
  fireEvent.click(up());
  await screen.findByText("Authors cannot vote on their own revisions.");
  vi.mocked(readCommunityFeedback).mockResolvedValue({ ...state(), current: { vote: "neutral", eligible: false, reason: "import_required" } });
  rerender(<CommunityFeedback revisionId="two" disabled={false} refreshKey="target" />);
  fireEvent.click(down());
  await screen.findByText("An acknowledged import of this exact revision is required.");
  vi.mocked(readCommunityFeedback).mockResolvedValue(state());
  rerender(<CommunityFeedback revisionId="three" authorId="reader" disabled={false} refreshKey="target" />);
  fireEvent.click(up());
  await screen.findByText("Authors cannot vote on their own revisions.");
  expect(sendCommunityVote).not.toHaveBeenCalled();
});

it("discards private replies when moving to a different candidate or stale lookup", async () => {
  let resolve!: (value: Feedback) => void;
  vi.mocked(readCommunityFeedback).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const { rerender } = render(<CommunityFeedback revisionId="one" disabled={false} refreshKey="target" />);
  fireEvent.click(up());
  rerender(<CommunityFeedback revisionId="two" disabled={true} refreshKey="new-target" />);
  resolve(state("like"));
  await waitFor(() => expect(up()).toBeDisabled());
  expect(up()).toHaveAttribute("aria-pressed", "false");
  expect(sendCommunityVote).not.toHaveBeenCalled();
});
