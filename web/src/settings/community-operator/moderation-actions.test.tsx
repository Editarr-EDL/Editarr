import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ModerationActions } from "./moderation-actions";
import { readOperatorInspection, type OperatorInspection } from "../../api/api-community-operator";
import { writeOperatorAction, writeOperatorRecompute, type OperatorIntent, type RecomputeIntent } from "../../api/api-community-operator-actions";
import { versionFixture } from "./test-fixtures";

const snapshot: OperatorInspection = { resource: "filter:filter-1", revision: 2,
  state: { filterId: "filter-1", versionId: "version-1", state: "active", mergedInto: null, quarantined: 0, served: 1, confidence: 0.5, contributorCount: 2 }, audit: [] };
const versionSnapshot: OperatorInspection = { resource: "version:version-1", revision: 4,
  state: { ...versionFixture, referenceSubmissionId: null }, audit: [] };
const intent: OperatorIntent = { version: 1, actionId: "12345678-1234-4234-8234-123456789abc", command: "quarantine",
  resource: "filter", targetId: "filter-1", expectedRevision: 2, reason: "Reviewed privacy report" };
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true,
    value: function (this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal"); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount(inspection = snapshot) {
  const applied = vi.fn(), denied = vi.fn();
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    const body = options?.method === "POST" ? JSON.parse(String(options.body)) : null;
    return Promise.resolve(Response.json(!body ? { version: 1, ...inspection } : path === "/admin/v1/recompute" ?
      { version: 1, actionId: body.actionId, versions: [{ versionId: "version-1", revision: 5, changed: true }] } :
      { version: 1, actionId: body.actionId, resource: inspection.resource, revision: inspection.revision + 1, outcome: "applied" }));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<ModerationActions inspection={inspection} enabled onApplied={applied} onAccessFailure={denied} />);
  return { fetcher, applied, denied };
}
it("freshly inspects before confirmation, requires a reason and cancels without writes", async () => {
  const { fetcher } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine filter" }));
  await screen.findByRole("dialog");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: "   " } });
  expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(fetcher).toHaveBeenCalledTimes(1);
});
it("prevents duplicate clicks and refreshes after a validated audited result", async () => {
  const { fetcher, applied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine filter" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  const confirm = screen.getByRole("button", { name: "Confirm action" }); fireEvent.click(confirm); fireEvent.click(confirm);
  await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "POST"); expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({ expectedRevision: 2, reason: intent.reason, command: "quarantine",
    resource: "filter", targetId: "filter-1" });
  expect(writes[0][1]).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { "X-Community-Operator": "1", "Content-Type": "application/json" } });
});
it("retries the identical saved intent after uncertain response and never silently replaces it", async () => {
  const { fetcher, applied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine filter" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  fetcher.mockImplementationOnce(() => Promise.reject(new TypeError("offline")));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await screen.findByText(/Outcome unconfirmed/);
  expect(screen.getByLabelText("Operator reason")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Close without confirming outcome" }));
  expect(screen.getByRole("button", { name: "Quarantine filter" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Review saved action" }));
  fireEvent.click(screen.getByRole("button", { name: "Retry saved action" })); await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "POST"); expect(writes).toHaveLength(2);
  expect(writes[0][1]?.body).toBe(writes[1][1]?.body);
});
it.each([409, 403])("forces review on conflict or policy refusal %s without clearing Access", async status => {
  const { fetcher, applied, denied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine filter" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({ error: { code: status === 409 ? "moderation_conflict" : "moderation_ineligible" } }, { status })));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  expect(denied).not.toHaveBeenCalled(); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("refuses changed fresh state before any mutation", async () => {
  const { fetcher, applied } = mount(); fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({ version: 1, ...snapshot, revision: 3 })));
  fireEvent.click(screen.getByRole("button", { name: "Quarantine filter" })); await screen.findByText(/State changed/);
  expect(applied).toHaveBeenCalledTimes(1); expect(fetcher).toHaveBeenCalledTimes(1); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it.each([401, 403])("clears protected UI on mutation Access denial %s", async status => {
  const { fetcher, denied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine filter" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({ error: { code: "operator_denied" } }, { status })));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(denied).toHaveBeenCalledWith(status));
});
it("validates exact action result and filter vote inspection; rejects private extras", async () => {
  const signal = new AbortController().signal;
  for (const value of [{ version: 1, actionId: intent.actionId, resource: "filter:other", revision: 3, outcome: "applied" },
    { version: 1, actionId: intent.actionId, resource: snapshot.resource, revision: 2, outcome: "applied" }]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(value)));
    await expect(writeOperatorAction(intent, signal)).rejects.toThrow();
  }
  const vote = { version: 1, resource: "vote:filter-1:contributor-1", revision: 0,
    state: { filterId: "filter-1", contributorId: "contributor-1", vote: "down", excluded: 1, updatedAt: 1 }, audit: [] };
  const fetcher = vi.fn().mockResolvedValue(Response.json(vote)); vi.stubGlobal("fetch", fetcher);
  await expect(readOperatorInspection("vote", "filter-1", signal, "contributor-1")).resolves.toMatchObject(vote);
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/moderation/vote/filter-1/contributor-1");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...vote, state: { ...vote.state, discordUserId: "secret" } })));
  await expect(readOperatorInspection("vote", "filter-1", signal, "contributor-1")).rejects.toThrow();
});

it("does not send writes for invalid reasons, targets or vote scopes", () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  for (const invalid of [{ ...intent, reason: " " }, { ...intent, reason: "x".repeat(1001) },
    { ...intent, reason: "bad\u0000reason" }, { ...intent, contributorId: "contributor-1" },
    { ...intent, command: "exclude_vote" as const }, { ...intent, targetId: "../secret" }, { ...intent, resource: "report" as const },
    { ...intent, intoVersionId: "version-2" }, { ...intent, command: "merge_version" as const, resource: "version" as const },
    { ...intent, command: "approve_alias" as const, resource: "alias_suggestion" as const, newAliasId: "remaster" },
    { ...intent, resource: "attestation" as const, targetId: "submission-1" }])
    expect(() => writeOperatorAction(invalid, new AbortController().signal)).toThrow();
  expect(fetcher).not.toHaveBeenCalled();
});

it("bounds mutation errors and responses and clears malformed forbidden responses", async () => {
  const signal = new AbortController().signal;
  for (const response of [new Response(" ".repeat(131073), { headers: { "Content-Type": "application/json" } }),
    Response.json({ version: 1, actionId: intent.actionId, resource: snapshot.resource, revision: 3, outcome: "applied", token: "secret" })]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response)); await expect(writeOperatorAction(intent, signal)).rejects.toMatchObject({ status: 503 });
  }
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Forbidden", { status: 403 })));
  await expect(writeOperatorAction(intent, signal)).rejects.toMatchObject({ status: 403 });
});
it("sends a merge with its target and the version resource", async () => {
  const { fetcher, applied } = mount(versionSnapshot);
  expect(screen.getByRole("button", { name: "Confirm version" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Merge into another version" })); await screen.findByRole("dialog");
  fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: "Duplicate cut" } });
  expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Target version ID (same work)"), { target: { value: "version-2" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(applied).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "POST")!;
  expect(JSON.parse(String(write[1]?.body))).toMatchObject({ command: "merge_version", resource: "version", targetId: "version-1",
    expectedRevision: 4, intoVersionId: "version-2" });
});

it("recomputes one version through the recompute endpoint without an expected revision", async () => {
  const { fetcher, applied } = mount(versionSnapshot);
  fireEvent.click(screen.getByRole("button", { name: "Recompute version" })); await screen.findByRole("dialog");
  fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: "Policy changed" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(applied).toHaveBeenCalled());
  expect(screen.getByRole("status")).toHaveTextContent("Master revision 5 (changed)");
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "POST")!;
  expect(write[0]).toBe("/admin/v1/recompute");
  expect(JSON.parse(String(write[1]?.body))).toEqual({ version: 1, actionId: expect.any(String), versionIds: ["version-1"], reason: "Policy changed" });
});

it("approves an alias suggestion as a new alias and offers no actions once decided", async () => {
  const suggestion: OperatorInspection = { resource: "alias_suggestion:suggestion-1", revision: 0, audit: [],
    state: { suggestionId: "suggestion-1", normalized: "remastered", text: "Remastered", submissionId: "submission-1", state: "pending", aliasId: null, createdAt: 1 } };
  const { fetcher, applied } = mount(suggestion);
  expect(screen.getByRole("button", { name: "Reject alias" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Approve alias" })); await screen.findByRole("dialog");
  fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: "Common label" } });
  fireEvent.click(screen.getByLabelText("Create a new alias"));
  fireEvent.change(screen.getByLabelText(/New alias ID/), { target: { value: "remastered" } });
  expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("New alias label"), { target: { value: "Remastered" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(applied).toHaveBeenCalled());
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "POST")!;
  expect(JSON.parse(String(write[1]?.body))).toMatchObject({ command: "approve_alias", resource: "alias_suggestion",
    newAliasId: "remastered", label: "Remastered" });
  cleanup(); mount({ ...suggestion, state: { ...suggestion.state, state: "approved", aliasId: "remastered" } });
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("addresses attestations by encoded submission and entry ID", async () => {
  const attestation: OperatorInspection = { resource: "attestation:submission-1:entry-1", revision: 1, audit: [],
    state: { submissionId: "submission-1", entryId: "entry-1", kind: "support", category: "violence", startMs: 1, endMs: 2,
      weight: 1, ignoredReason: null, quarantined: 1, filterId: "filter-1" } };
  const { fetcher } = mount(attestation);
  fireEvent.click(screen.getByRole("button", { name: "Restore attestation" })); await screen.findByRole("dialog");
  expect(fetcher.mock.calls[0][0]).toBe("/admin/v1/moderation/attestation/submission-1%3Aentry-1");
});

it("validates recompute intents and exact job results", async () => {
  const signal = new AbortController().signal, actionId = intent.actionId;
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  for (const invalid of [{ versionIds: [] }, { versionIds: ["a", "a"] }, { versionIds: ["a"], after: "b" }, { versionIds: "all" as const, reason: " " }])
    expect(() => writeOperatorRecompute({ version: 1, actionId, reason: "r", ...invalid } as RecomputeIntent, signal)).toThrow();
  expect(fetcher).not.toHaveBeenCalled();
  fetcher.mockResolvedValueOnce(Response.json({ version: 1, jobId: actionId, complete: false, nextAfter: "version-9" }));
  await expect(writeOperatorRecompute({ version: 1, actionId, versionIds: "all", after: "version-1", reason: "r" }, signal))
    .resolves.toMatchObject({ nextAfter: "version-9" });
  fetcher.mockResolvedValueOnce(Response.json({ version: 1, jobId: "other", complete: true, nextAfter: null }));
  await expect(writeOperatorRecompute({ version: 1, actionId, versionIds: "all", reason: "r" }, signal)).rejects.toThrow();
});
