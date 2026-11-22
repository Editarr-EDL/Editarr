import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { fetchSavedReview, fetchSavedReviews } from "../api/api-reviews";
import { mediaItem, savedReview } from "../test/app-test-fixtures";
import { useReviewSelection } from "./review-selection";

vi.mock("../api/api-reviews", () => ({ fetchSavedReview: vi.fn(), fetchSavedReviews: vi.fn() }));

it("refreshes the review list after version identification and keeps the unsaved draft", async () => {
  const latest = savedReview({ id: "exact-review", name: "Exact review" });
  const initial = savedReview({ id: "initial-review", name: "Initially selected" });
  vi.mocked(fetchSavedReviews).mockImplementation(async (_id, options) =>
    options?.stable_version_id ? [latest] : [initial]);
  vi.mocked(fetchSavedReview).mockResolvedValue(initial);
  const callbacks = {
    setBusy: vi.fn(), setError: vi.fn(), setEditingReviewName: vi.fn(), resetMetadata: vi.fn(),
  };
  const { result, rerender } = renderHook(({ stableVersionId }: { stableVersionId?: string }) =>
    useReviewSelection({
      item: mediaItem, mediaSourceId: "source-1", stableVersionId,
      sort: "updated_at", direction: "desc", catalogMcfDocument: () => initial.mcf_document, ...callbacks,
    }), { initialProps: {} });
  await waitFor(() => expect(result.current.draft?.name).toBe(initial.name));
  act(() => result.current.setDraft((draft) => draft ? { ...draft, name: "My unsaved changes" } : draft));

  rerender({ stableVersionId: "exact-version" });

  await waitFor(() => expect(result.current.reviews).toEqual([latest]));
  expect(result.current.draft?.name).toBe("My unsaved changes");
  expect(result.current.selected?.id).toBe(initial.id);
});
