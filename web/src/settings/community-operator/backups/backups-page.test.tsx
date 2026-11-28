import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { renderOperator, stubOperatorFetch } from "../test-render";
import { backupsFixture } from "../test-fixtures-pages";
import { BackupsPage } from "./backups-page";
import { configErrors } from "./schedule-form";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const config = { enabled: true, frequency: "daily" as const, atUtc: "03:30", keepLast: 14, keepWeekly: 8, destination: "nightly", integrityCheck: true };

it("validates the schedule like the service", () => {
  expect(configErrors(config)).toEqual({});
  expect(Object.keys(configErrors({ ...config, atUtc: "24:00", keepLast: 0, keepWeekly: 105, destination: "../etc" }))).toEqual(
    ["atUtc", "keepLast", "keepWeekly", "destination"]);
});

it("saves the edited schedule, reports refusals and links verified downloads", async () => {
  let refuse = true;
  const fetcher = stubOperatorFetch((_path, init) => init?.method === "PUT" ? refuse ?
    Response.json({ error: { code: "backup_destination_outside_root" } }, { status: 400 }) : backupsFixture : undefined);
  renderOperator(<BackupsPage />);
  fireEvent.change(await screen.findByLabelText("Time (UTC)", {}, { timeout: 4000 }), { target: { value: "04:15" } });
  fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent("Refused: the destination is outside the backup root.");
  refuse = false;
  fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  expect(await screen.findByText("Schedule saved and audited.", {}, { timeout: 4000 })).toBeInTheDocument();
  const put = fetcher.mock.calls.filter(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put[1][1]?.body))).toEqual({ ...config, atUtc: "04:15" });
  expect(screen.getAllByRole("link", { name: /^Download/ })[0]).toHaveAttribute("href", "/admin/v1/backups/bk-27/download");
  expect(screen.getByText("destination not writable")).toBeInTheDocument();
});

it("disables backups without a configured root and polls while a run is in progress", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let reads = 0;
  stubOperatorFetch(path => path === "/admin/v1/backups" ? (reads += 1, { ...backupsFixture, running: reads === 1,
    destination: { ...backupsFixture.destination, root: null, configured: false } }) : undefined);
  renderOperator(<BackupsPage />);
  expect(await screen.findByRole("button", { name: "Backup running…" })).toBeDisabled();
  expect(screen.getByRole("note")).toHaveTextContent("Backups are disabled until the operator sets COMMUNITY_BACKUP_ROOT");
  await vi.advanceTimersByTimeAsync(3100);
  await waitFor(() => expect(reads).toBe(2));
  expect(await screen.findByRole("button", { name: "Run backup now" })).toBeDisabled();
  vi.useRealTimers();
});

it("keeps polling a running backup after a failed refresh without overlapping reads", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let reads = 0, inFlight = 0, overlap = false;
  stubOperatorFetch(path => {
    if (path !== "/admin/v1/backups") return undefined;
    reads += 1; inFlight += 1; if (inFlight > 1) overlap = true;
    queueMicrotask(() => { inFlight -= 1; });
    return reads === 2 ? Response.json({ error: { code: "unavailable" } }, { status: 503 }) : { ...backupsFixture, running: reads < 4 };
  });
  renderOperator(<BackupsPage />);
  expect(await screen.findByRole("button", { name: "Backup running…" })).toBeDisabled();
  await vi.advanceTimersByTimeAsync(3100);
  await waitFor(() => expect(reads).toBe(2));
  expect(await screen.findByText("Stale snapshot. Refresh unavailable.")).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(3100);
  await waitFor(() => expect(reads).toBe(3));
  await vi.advanceTimersByTimeAsync(3100);
  await waitFor(() => expect(reads).toBe(4));
  expect(await screen.findByRole("button", { name: "Run backup now" })).toBeEnabled();
  await vi.advanceTimersByTimeAsync(6200);
  expect(reads).toBe(4); expect(overlap).toBe(false);
  vi.useRealTimers();
});

it.each([["operator_not_allowed", true], ["operator_token_invalid", true], ["operator_csrf_denied", false], ["backup_running", false]] as const)(
  "a 403 %s from a backup mutation ends the session only for authorization failures", async (code, ends) => {
    stubOperatorFetch((_path, init) => init?.method === "POST" ? Response.json({ error: { code } }, { status: 403 }) : undefined);
    const { services } = renderOperator(<BackupsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Run backup now" }, { timeout: 4000 }));
    if (ends) await waitFor(() => expect(services.onAccessFailure).toHaveBeenCalledWith(403));
    else {
      expect(await screen.findByRole("alert")).toHaveTextContent("Refused:");
      expect(services.onAccessFailure).not.toHaveBeenCalled();
    }
  });
