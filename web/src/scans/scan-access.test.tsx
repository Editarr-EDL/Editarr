import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Scan } from "../api/api-scan-types";
import { knownCapabilities } from "../auth/capabilities";
import type { Principal } from "../auth/session";
import { ScanDeleteDialog, ScanSelectionList } from "./scan-components";
import { ScansFloatingPanel } from "./scans-floating-panel";

const state = vi.hoisted(() => ({ principal: null as Principal | null }));
vi.mock("../auth/auth-gate", () => ({ usePrincipal: () => state.principal }));
vi.mock("./sharing-dialog", () => ({ ShareAction: () => null }));

const scan = { id: "shared", scan_id: 1, name: "Shared evidence", status: "complete", is_own: false,
  created_at: "2026-10-24T00:00:00Z", updated_at: "2026-10-24T00:00:00Z" } as Scan;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); state.principal = null; });

it("keeps shared evidence but omits other-user and unknown-ownership mutation controls", () => {
  state.principal = { capabilities: knownCapabilities(["scans.rename.own", "scans.delete.own"]) } as Principal;
  const { rerender } = render(<ScanSelectionList scans={[scan]} onDeleteScan={vi.fn()} />);
  expect(screen.getByText(/Shared evidence.*complete/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /Rename|Delete/ })).not.toBeInTheDocument();
  rerender(<ScanSelectionList scans={[{ ...scan, is_own: undefined }]} onDeleteScan={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /Rename|Delete/ })).not.toBeInTheDocument();
  rerender(<ScanSelectionList scans={[{ ...scan, is_own: true }]} onDeleteScan={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Rename Shared evidence · Scan #1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Delete Shared evidence · Scan #1" })).toBeVisible();
});

it("allows other-user controls only with their explicit grants", () => {
  state.principal = { capabilities: knownCapabilities(["scans.rename.other", "scans.delete.other"]) } as Principal;
  render(<ScanSelectionList scans={[scan]} onDeleteScan={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Rename Shared evidence · Scan #1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Delete Shared evidence · Scan #1" })).toBeVisible();
});

it("shows a private-reference blocker and cannot submit a deletion", async () => {
  const requests = vi.fn(async () => new Response(JSON.stringify({ scan_id: scan.id, active_exports: [],
    reviews: [], has_hidden_references: true }), { status: 200 }));
  vi.stubGlobal("fetch", requests);
  const onDelete = vi.fn();
  render(<ScanDeleteDialog scan={scan} deleting={false} onClose={vi.fn()} onDelete={onDelete} />);
  await waitFor(() => expect(screen.getByText("Saved reviews you cannot view")).toBeVisible());
  expect(screen.getByRole("button", { name: "Delete scan" })).toBeDisabled();
  expect(requests).toHaveBeenCalledTimes(1);
  expect(onDelete).not.toHaveBeenCalled();
  expect(document.body.textContent).not.toMatch(/private-title|private-id/);
});

it("blocks deletion while a community operation is in progress without exposing private details", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ scan_id: scan.id,
    active_exports: [], reviews: [], has_hidden_references: false, community_in_progress: true }),
  { status: 200 })));
  const onDelete = vi.fn();
  render(<ScanDeleteDialog scan={scan} deleting={false} onClose={vi.fn()} onDelete={onDelete} />);
  await waitFor(() => expect(screen.getByText("A community operation for this scan is still in progress")).toBeVisible());
  const submit = screen.getByRole("button", { name: "Delete scan" });
  expect(submit).toBeDisabled();
  fireEvent.click(submit);
  expect(onDelete).not.toHaveBeenCalled();
  expect(screen.queryByText(/This permanently removes/)).not.toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/community\.example|operation-id|artifact-id/);
});

it("does not mount or request queue data without a job-view grant", () => {
  state.principal = { capabilities: knownCapabilities(["scans.findings.read"]) } as Principal;
  const requests = vi.fn();
  vi.stubGlobal("fetch", requests);
  const { container } = render(<ScansFloatingPanel />);
  expect(container).toBeEmptyDOMElement();
  expect(requests).not.toHaveBeenCalled();
});

it("allows deletion when no review, export or community operation is in progress", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ scan_id: scan.id,
    active_exports: [], reviews: [], has_hidden_references: false, community_in_progress: false }), { status: 200 })));
  const onDelete = vi.fn();
  render(<ScanDeleteDialog scan={scan} deleting={false} onClose={vi.fn()} onDelete={onDelete} />);
  await screen.findByText("This permanently removes the scan and its findings.");
  fireEvent.click(screen.getByRole("button", { name: "Delete scan" }));
  expect(onDelete).toHaveBeenCalledOnce();
});

it("shows own queue actions and acquisition feedback without global controls or requests", async () => {
  state.principal = { capabilities: knownCapabilities(["scan_jobs.view.own", "scan_jobs.cancel.own", "scan_jobs.resume.own"]) } as Principal;
  const requests = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).endsWith("/queue") ? [
    { ...scan, id: "mine", item_name: "My file", queue_id: 1, is_own: true, status: "orphaned" },
    { ...scan, item_name: "Hidden job", queue_id: 2, status: "scanning" },
  ] : []), { status: 200 }));
  vi.stubGlobal("fetch", requests);
  render(<ScansFloatingPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Open scans queue and history" }));
  await waitFor(() => expect(screen.getByText("My file")).toBeVisible());
  expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Delete" })).toBeVisible();
  expect(screen.queryByText("Hidden job")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Move.*up|Move.*down/ })).not.toBeInTheDocument();
  expect(requests.mock.calls.every(([input]) => /\/scans\/(queue|history)$/.test(String(input)))).toBe(true);
});