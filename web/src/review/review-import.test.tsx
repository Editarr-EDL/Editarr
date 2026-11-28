import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ImportDialog } from "./review-import";
import { AuthenticationGate } from "../auth/auth-gate";
import { json, signedInStatus } from "../test/app-test-fixtures";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const orphanMessage = "This file must belong to a Jellyfin item. Open its Jellyfin item and import the file there.";

function renderDialog(onImported = vi.fn()) {
  render(<AuthenticationGate><ImportDialog
    target={{ source: "jellyfin", item_id: "item-1", media_source_id: "version-2" }}
    itemName="Synthetic Movie"
    versionName="Director's Cut"
    durationSeconds={100}
    creditsScans={[]}
    onImported={onImported}
  /></AuthenticationGate>);
  return onImported;
}

it("names the Jellyfin item and version the file imports into", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(signedInStatus({ capabilities: ["reviews.import.upload"] }))));
  renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "Import" }));
  expect(screen.getByText("Imports into Jellyfin item Synthetic Movie, version Director's Cut")).toBeVisible();
});

it("shows the server's Jellyfin-item refusal in the dialog and reports no import", async () => {
  const requests = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/edl-imports/upload")
    ? json({ detail: { code: "jellyfin_item_required", message: orphanMessage } }, 400)
    : json(signedInStatus({ capabilities: ["reviews.import.upload"] })));
  vi.stubGlobal("fetch", requests);
  const onImported = renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "Import" }));
  fireEvent.change(screen.getByLabelText("Import review name"), { target: { value: "Orphan" } });
  fireEvent.change(screen.getByLabelText("EDL file"), { target: { files: [new File(["1 2 1\n"], "orphan.edl")] } });
  fireEvent.click(screen.getByRole("button", { name: "Import EDL" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(orphanMessage);
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(requests.mock.calls.some(([input]) => String(input).endsWith("/edl-imports/upload"))).toBe(true);
  expect(onImported).not.toHaveBeenCalled();
});

it("uploads a VideoSkip file with attribution for verification", async () => {
  const imported = { review: null, scan: { id: "imported", reuse_pending: true }, issue_count: 0, issues: [] };
  const requests = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) =>
    String(input).endsWith("/videoskip-imports/upload")
      ? json(imported, 201) : json(signedInStatus({ capabilities: ["reviews.import.upload"] })));
  vi.stubGlobal("fetch", requests);
  const onImported = renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "Import" }));
  fireEvent.click(screen.getByRole("button", { name: /^VideoSkip$/ }));
  expect(screen.getByText(/verify timing on the selected cut/i)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Import review name"), { target: { value: "Exchange contribution" } });
  fireEvent.change(screen.getByLabelText("Exchange file URL"), { target: { value: "https://videoskip.herokuapp.com/exchange/skip/54/" } });
  fireEvent.change(screen.getByLabelText("Uploader"), { target: { value: "Contributor" } });
  const fileInput = screen.getByLabelText("VideoSkip file");
  expect(fileInput).toHaveAttribute("accept", expect.stringContaining(".skp"));
  fireEvent.change(fileInput, { target: { files: [new File(["0:00:02 --> 0:00:04\nsex 2"], "movie.skp")] } });
  fireEvent.click(screen.getByRole("button", { name: "Import VideoSkip" }));
  await vi.waitFor(() => expect(onImported).toHaveBeenCalledWith(imported));
  const request = requests.mock.calls.find(([input]) => String(input).endsWith("/videoskip-imports/upload"));
  const form = request?.[1]?.body as FormData;
  expect((form.get("file") as File).name).toBe("movie.skp");
  expect(JSON.parse(String(form.get("options")))).toEqual({
    name: "Exchange contribution", target: { source: "jellyfin", item_id: "item-1", media_source_id: "version-2" },
    attribution: { source_url: "https://videoskip.herokuapp.com/exchange/skip/54/", uploader: "Contributor" },
  });
});
