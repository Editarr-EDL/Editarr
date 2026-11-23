import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import App from "../App";
import type { EditarrItem } from "../api/api-stable-media";
import { AuthenticationGate } from "../auth/auth-gate";
import { completeScan, installProductionApi, json, mediaItem } from "./app-test-fixtures";
import { openWorkflowStep } from "./review-workflow-test-helpers";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

it.each([true, false])("opens an exact alternate version with visible history %s", async (history) => {
  const alternative = { ...mediaItem.versions[0], id: "source-2", name: "Alternate cut" };
  const item = { ...mediaItem, versions: [...mediaItem.versions, alternative], media_source_count: 2 };
  const version = {
    id: "alternate-cut", jellyfin_item_id: item.id, media_source_id: alternative.id,
    label: alternative.name, available: true, active_item_id: item.id,
    active_media_source_id: alternative.id, scans: [], reviews: [],
  };
  const identity: EditarrItem = {
    title_id: "exact-title", name: item.name, active_item: item, active_items: [item],
    versions: history ? [version] : [], selected_version_id: null, choose_version: false,
    current_versions: [{ item_id: item.id, media_source_id: alternative.id, version_id: version.id }],
  };
  const fallback = installProductionApi();
  const reviews = vi.fn();
  const enqueue = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/editarr-items/exact-title") {
      expect(url.searchParams.get("version_id")).toBe(version.id);
      return json(identity);
    }
    if (url.pathname === "/api/library-items/movie-1/reviews") {
      reviews(Object.fromEntries(url.searchParams));
      return json([]);
    }
    if (url.pathname === "/api/scans" && init?.method === "POST") {
      enqueue(JSON.parse(String(init.body)));
      return json({ ...completeScan(), id: "queued-alt", scan_id: 15, status: "queued" }, 201);
    }
    return fallback(input, init);
  }));

  window.location.hash = "#title/exact-title/version/alternate-cut";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  const selector = await screen.findByRole("combobox", { name: "Media version" });
  expect(selector).toHaveValue(alternative.id);
  await openWorkflowStep("Review");
  await waitFor(() => expect(reviews).toHaveBeenCalledWith(expect.objectContaining({
    media_source_id: alternative.id, stable_version_id: version.id,
  })));
  await openWorkflowStep("Scans");
  fireEvent.click(screen.getByRole("button", { name: "New Scan" }));
  const dialog = screen.getByRole("dialog", { name: "New Scan" });
  expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveValue(alternative.id);
  const queue = within(dialog).getByRole("button", { name: "Add to Queue" });
  await waitFor(() => expect(queue).toBeEnabled());
  fireEvent.click(queue);
  await waitFor(() => expect(enqueue).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
    jellyfin_item_id: item.id, media_source_id: alternative.id,
  })));
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
  expect(reviews.mock.calls.every(([target]) => target.media_source_id === alternative.id)).toBe(true);
  fireEvent.change(selector, { target: { value: mediaItem.versions[0].id } });
  expect(selector).toHaveValue(mediaItem.versions[0].id);
});

it("waits for an explicit version response and refuses a different catalog version", async () => {
  const identity: EditarrItem = {
    title_id: "exact-title", name: mediaItem.name, active_item: mediaItem, active_items: [mediaItem],
    versions: [], selected_version_id: null, choose_version: false,
    current_versions: [{ item_id: mediaItem.id, media_source_id: "source-1", version_id: "current" }],
  };
  const fallback = installProductionApi();
  let finishRequest: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = new URL(String(input), "http://editarr.test");
    if (url.pathname === "/api/editarr-items/exact-title") {
      if (url.searchParams.get("version_id") === "missing") {
        return new Promise<Response>((resolve) => { finishRequest = resolve; });
      }
      return json(identity);
    }
    return fallback(input, init);
  }));

  window.location.hash = "#title/exact-title";
  render(<AuthenticationGate><App /></AuthenticationGate>);
  await openWorkflowStep("Scans");
  expect(screen.getByRole("button", { name: "New Scan" })).toBeVisible();

  await act(async () => { window.location.hash = "#title/exact-title/version/missing"; });
  await waitFor(() => expect(finishRequest).toBeDefined());
  expect(screen.getByText("Loading item…")).toBeVisible();
  expect(screen.queryByRole("button", { name: "New Scan" })).not.toBeInTheDocument();
  await act(async () => { finishRequest?.(json(identity)); });
  expect(await screen.findByRole("alert")).toHaveTextContent("This library item is unavailable.");
  expect(screen.queryByRole("button", { name: "New Scan" })).not.toBeInTheDocument();
});

it.each([["missing-source"], ["source-1", "source-2"]])(
  "refuses an unconfirmed or ambiguous live source mapping %j", async (...sources) => {
    const alternative = { ...mediaItem.versions[0], id: "source-2", name: "Alternate cut" };
    const item = { ...mediaItem, versions: [...mediaItem.versions, alternative], media_source_count: 2 };
    const identity: EditarrItem = {
      title_id: "exact-title", name: item.name, active_item: item, active_items: [item],
      versions: [], selected_version_id: null, choose_version: false,
      current_versions: sources.map((sourceId) => ({
        item_id: item.id, media_source_id: sourceId, version_id: "uncertain-cut",
      })),
    };
    const fallback = installProductionApi();
    vi.stubGlobal("fetch", vi.fn((input: string | URL | Request, init?: RequestInit) => (
      String(input).includes("/api/editarr-items/exact-title") ? json(identity) : fallback(input, init)
    )));

    window.location.hash = "#title/exact-title/version/uncertain-cut";
    render(<AuthenticationGate><App /></AuthenticationGate>);
    expect(await screen.findByRole("alert")).toHaveTextContent("This library item is unavailable.");
    expect(screen.queryByRole("combobox", { name: "Media version" })).not.toBeInTheDocument();
  },
);
