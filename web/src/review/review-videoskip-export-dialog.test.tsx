import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { SavedReview } from "../api/api-review-types";
import { VideoSkipExportDialog } from "./review-videoskip-export-dialog";

const review = {
  id: "review/1", name: "Theatrical", revision: 5, policy_owner_id: 9, policy_revision: 3,
  filters: [{ id: "filter-2", label: "Brief line", start_seconds: 3.785, end_seconds: 3.786 }],
} as SavedReview;
const token = { revision: 5, policy_owner_id: 9, policy_revision: 3 };
const ready = {
  eligible: true, filter_count: 2, exported_count: 1, issue_count: 1,
  issues: [{ code: "test-issue", filter_id: "filter-2", message: "A timing range was too short.", omitted: true }],
};

function open() {
  const conflict = vi.fn(async () => {});
  const close = vi.fn();
  render(<VideoSkipExportDialog review={review} onConflict={conflict} onClose={close} />);
  return {
    check: screen.getByRole("button", { name: "Check compatibility" }),
    download: screen.getByRole("button", { name: "Download VideoSkip" }),
    time: screen.getByLabelText("Reference time"),
    description: screen.getByLabelText("Reference description"),
    conflict, close,
  };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("checks the saved review and reference before downloading the named attachment", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).endsWith("/preflight")
    ? Response.json(ready)
    : new Response("0:00:07.123 --> 0:00:08.456\nprofanity audio 2\n", {
      headers: { "content-disposition": "attachment; filename*=UTF-8''Example%20Cut.skp" },
    }));
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:videoskip");
    static revokeObjectURL = vi.fn();
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const { check, download, time, description } = open();
  expect(download).toBeDisabled();
  fireEvent.change(time, { target: { value: "00:01:02.123" } });
  expect(check).toBeDisabled();
  fireEvent.change(description, { target: { value: "  Opening card  " } });
  fireEvent.click(check);
  expect(await screen.findByRole("status")).toHaveTextContent("1 of 2 selected filters");
  expect(screen.getByText(/A timing range was too short/)).toHaveTextContent("Brief line · 00:00:03.785 – 00:00:03.786. Omitted.");
  expect(screen.queryByRole("button", { name: /Publish/ })).toBeNull();
  fireEvent.click(download);
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect((click.mock.instances[0] as unknown as HTMLAnchorElement).download).toBe("Example Cut.skp");
  expect(fetch.mock.calls.map(([input, init]) => [String(input), init?.method, JSON.parse(String(init?.body))])).toEqual([
    ["/api/reviews/review%2F1/exports/videoskip/preflight", "POST", { ...token, reference_seconds: 62.123, reference_description: "Opening card" }],
    ["/api/reviews/review%2F1/exports/videoskip/download", "POST", { ...token, reference_seconds: 62.123, reference_description: "Opening card" }],
  ]);
});

it("requires a fresh preflight after reference edits and blocks an ineligible export", async () => {
  const fetch = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>()
    .mockResolvedValueOnce(Response.json(ready))
    .mockResolvedValueOnce(Response.json({ ...ready, eligible: false }));
  vi.stubGlobal("fetch", fetch);
  const { check, download, time, description } = open();
  fireEvent.click(check);
  await waitFor(() => expect(download).toBeEnabled());
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(token);
  fireEvent.change(time, { target: { value: "00:01:00" } });
  expect(screen.queryByRole("status")).toBeNull();
  expect(download).toBeDisabled();
  fireEvent.change(description, { target: { value: "Scene begins" } });
  fireEvent.click(check);
  expect(await screen.findByText("This review can't be downloaded as a VideoSkip file.")).toBeVisible();
  expect(download).toBeDisabled();
});

it("refreshes the review if the policy changed before download", async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(Response.json(ready))
    .mockResolvedValueOnce(Response.json({ detail: { code: "stale_review_policy", message: "Policy changed" } }, { status: 409 }));
  vi.stubGlobal("fetch", fetch);
  const { check, download, conflict, close } = open();
  fireEvent.click(check);
  await waitFor(() => expect(download).toBeEnabled());
  fireEvent.click(download);
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(conflict).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ code: "stale_review_policy" }));
});
