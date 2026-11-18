import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAuditEntry, fetchAuditPage, type AuditEntry, type AuditPage } from "../../api/api-audit";
import { ApiError } from "../../api/api-transport";
import type { PermissionRevision, Principal, UserId } from "../../auth/session";
import { AuditHistory } from "./audit-history";

const fixture = vi.hoisted(() => ({ principal: null as Principal | null }));
vi.mock("../../auth/auth-gate", () => ({ usePrincipal: () => fixture.principal }));
vi.mock("../../api/api-audit", async importOriginal => ({ ...await importOriginal<object>(), fetchAuditPage: vi.fn(), fetchAuditEntry: vi.fn() }));

const entry: AuditEntry = { event_id: "00000000-0000-4000-8000-000000000001", occurred_at: "2026-10-22T12:00:00Z", action: "api_key.issue", category: "security", outcome: "succeeded", actor_kind: "user", actor_id: 1, credential: "session", target_user_id: 4, media_key: null, resource: null, details: {} };
const page: AuditPage = { events: [entry], total: 1, pages: 1, page: 1, page_size: 25, retained_since: "2026-07-24T12:00:00Z", retention_days: 90 };

beforeEach(() => {
  vi.resetAllMocks();
  fixture.principal = { userId: 4 as UserId, revision: 1 as PermissionRevision, role: "admin",
    linkState: "verified", displayName: "Admin",
    capabilities: new Set(["audit.read"]) };
  vi.mocked(fetchAuditPage).mockResolvedValue(page);
  vi.mocked(fetchAuditEntry).mockResolvedValue(entry);
});
afterEach(cleanup);

describe("permission-scoped audit history", () => {
  it("does not request data for ordinary users, even with a stray audit capability", () => {
    fixture.principal = { ...fixture.principal!, role: "user" };
    render(<AuditHistory />);
    expect(screen.getByRole("alert")).toHaveTextContent("unavailable");
    expect(fetchAuditPage).not.toHaveBeenCalled();
    expect(fetchAuditEntry).not.toHaveBeenCalled();
  });

  it("shows attribution, server counts, and secret-free details", async () => {
    render(<AuditHistory />);
    expect(await screen.findByRole("link", { name: "api_key.issue" })).toHaveAttribute("href", `#/settings/audit/${entry.event_id}`);
    expect(screen.getByText("Account #1")).toBeVisible();
    expect(screen.getByText("Account #4")).toBeVisible();
    expect(screen.getByText("1 event")).toBeVisible();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
    cleanup();
    render(<AuditHistory eventId={entry.event_id} />);
    expect(await screen.findByRole("heading", { name: "api_key.issue" })).toBeVisible();
    expect(screen.getByText("session")).toBeVisible();
    expect(screen.queryByText(/password|token|secret/i)).not.toBeInTheDocument();
  });

  it("sends filters and pagination to the server", async () => {
    vi.mocked(fetchAuditPage).mockResolvedValue({ ...page, total: 30, pages: 2 });
    render(<AuditHistory />);
    await screen.findByText("30 events");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(fetchAuditPage).toHaveBeenLastCalledWith({ page: 2, page_size: 25 }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByLabelText("Target account ID"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "security" } });
    fireEvent.submit(screen.getByRole("form", { name: "Audit filters" }));
    await waitFor(() => expect(fetchAuditPage).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, target_user_id: 4, category: "security" }), expect.any(AbortSignal)));
  });

  it("discards previous detail during refresh, and shows expired detail as unavailable", async () => {
    render(<AuditHistory eventId={entry.event_id} />);
    await screen.findByRole("heading", { name: "api_key.issue" });
    vi.mocked(fetchAuditEntry).mockRejectedValue(new ApiError("Not found", 404));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(screen.queryByRole("heading", { name: "api_key.issue" })).not.toBeInTheDocument();
    expect(await screen.findByText(/Event no longer available/)).toBeVisible();
  });

  it("clears a retained event when refresh returns an empty page", async () => {
    render(<AuditHistory />);
    await screen.findByRole("link", { name: "api_key.issue" });
    vi.mocked(fetchAuditPage).mockResolvedValue({ ...page, events: [], total: 0, pages: 0 });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("No audit events in this selection.")).toBeVisible();
    expect(screen.queryByRole("link", { name: "api_key.issue" })).not.toBeInTheDocument();
  });

  it("aborts old reads and ignores late results after permissions change", async () => {
    let finish!: (value: AuditPage) => void;
    vi.mocked(fetchAuditPage).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<AuditHistory />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    const signal = vi.mocked(fetchAuditPage).mock.calls[0][1];
    vi.mocked(fetchAuditPage).mockRejectedValue(new ApiError("Denied", 403, "capability_denied"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("permissions may have changed");
    expect(signal.aborted).toBe(true);
    finish(page);
    await waitFor(() => expect(screen.queryByText("api_key.issue")).not.toBeInTheDocument());
  });

  it("shows retryable read contention without claiming permissions changed", async () => {
    render(<AuditHistory />);
    await screen.findByRole("link", { name: "api_key.issue" });
    vi.mocked(fetchAuditPage).mockRejectedValue(new ApiError("Busy", 503, "audit_history_busy"));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Audit history is busy. Try again.");
    expect(screen.queryByText(/permissions may have changed/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "api_key.issue" })).not.toBeInTheDocument();
    vi.mocked(fetchAuditPage).mockResolvedValue(page);
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByRole("link", { name: "api_key.issue" })).toBeVisible();
  });
});
