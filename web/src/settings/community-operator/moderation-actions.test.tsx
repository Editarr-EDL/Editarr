import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ModerationActions } from "./moderation-actions";
import { readOperatorInspection, writeOperatorAction, type OperatorInspection, type OperatorIntent } from "../../api/api-community-operator";

const snapshot: OperatorInspection = { resource: "revision:revision-1", revision: 2,
  state: { revisionId: "revision-1", contributorId: "contributor-1", state: "published", authorState: "active", imports: 1, likes: 0, dislikes: 0 }, audit: [] };
const intent: OperatorIntent = { version: 1, actionId: "12345678-1234-4234-8234-123456789abc", command: "quarantine",
  targetId: "revision-1", expectedRevision: 2, reason: "Reviewed privacy report" };
beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true,
    value: function (this: HTMLDialogElement) { this.open = true; } });
});
afterEach(() => { cleanup(); Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal"); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function mount() {
  const applied = vi.fn(), denied = vi.fn();
  const fetcher = vi.fn((_path: string, options?: RequestInit) => Promise.resolve(Response.json(options?.method === "POST" ?
    { version: 1, actionId: JSON.parse(String(options.body)).actionId, resource: snapshot.resource, revision: 3, outcome: "applied" } : { version: 1, ...snapshot })));
  vi.stubGlobal("fetch", fetcher);
  render(<ModerationActions inspection={snapshot} enabled onApplied={applied} onAccessFailure={denied} />);
  return { fetcher, applied, denied };
}
it("freshly inspects before confirmation, requires a reason and cancels without writes", async () => {
  const { fetcher } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine revision" }));
  await screen.findByRole("dialog");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: "   " } });
  expect(screen.getByRole("button", { name: "Confirm action" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(fetcher).toHaveBeenCalledTimes(1);
});
it("prevents duplicate clicks and refreshes after a validated audited result", async () => {
  const { fetcher, applied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine revision" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  const confirm = screen.getByRole("button", { name: "Confirm action" }); fireEvent.click(confirm); fireEvent.click(confirm);
  await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "POST"); expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({ expectedRevision: 2, reason: intent.reason, command: "quarantine" });
  expect(writes[0][1]).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { "X-Community-Operator": "1", "Content-Type": "application/json" } });
});
it("retries the identical saved intent after uncertain response and never silently replaces it", async () => {
  const { fetcher, applied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine revision" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  fetcher.mockImplementationOnce(() => Promise.reject(new TypeError("offline")));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await screen.findByText(/Outcome unconfirmed/);
  expect(screen.getByLabelText("Operator reason")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Close without confirming outcome" }));
  expect(screen.getByRole("button", { name: "Quarantine revision" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Review saved action" }));
  fireEvent.click(screen.getByRole("button", { name: "Retry saved action" })); await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  const writes = fetcher.mock.calls.filter(([, options]) => options?.method === "POST"); expect(writes).toHaveLength(2);
  expect(writes[0][1]?.body).toBe(writes[1][1]?.body);
});
it.each([409, 403])("forces review on conflict or policy refusal %s without clearing Access", async status => {
  const { fetcher, applied, denied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine revision" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({ error: { code: status === 409 ? "moderation_conflict" : "moderation_ineligible" } }, { status })));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  expect(denied).not.toHaveBeenCalled(); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("refuses changed fresh state before any mutation", async () => {
  const { fetcher, applied } = mount(); fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({ version: 1, ...snapshot, revision: 3 })));
  fireEvent.click(screen.getByRole("button", { name: "Quarantine revision" })); await screen.findByText(/State changed/);
  expect(applied).toHaveBeenCalledTimes(1); expect(fetcher).toHaveBeenCalledTimes(1); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it.each([401, 403])("clears protected UI on mutation Access denial %s", async status => {
  const { fetcher, denied } = mount(); fireEvent.click(screen.getByRole("button", { name: "Quarantine revision" }));
  await screen.findByRole("dialog"); fireEvent.change(screen.getByLabelText("Operator reason"), { target: { value: intent.reason } });
  fetcher.mockImplementationOnce(() => Promise.resolve(Response.json({ error: { code: "operator_denied" } }, { status })));
  fireEvent.click(screen.getByRole("button", { name: "Confirm action" })); await waitFor(() => expect(denied).toHaveBeenCalledWith(status));
});
it("validates exact action result and numeric vote inspection; rejects private extras", async () => {
  const signal = new AbortController().signal;
  for (const value of [{ version: 1, actionId: intent.actionId, resource: "revision:other", revision: 3, outcome: "applied" },
    { version: 1, actionId: intent.actionId, resource: snapshot.resource, revision: 2, outcome: "applied" }]) {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(value)));
    await expect(writeOperatorAction(intent, signal)).rejects.toThrow();
  }
  const vote = { version: 1, resource: "vote:contributor-1:revision-1", revision: 0,
    state: { contributorId: "contributor-1", revisionId: "revision-1", value: "like", excluded: 1 }, audit: [] };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(vote)));
  await expect(readOperatorInspection("vote", "contributor-1", signal, "revision-1")).resolves.toMatchObject(vote);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...vote, state: { ...vote.state, discordUserId: "secret" } })));
  await expect(readOperatorInspection("vote", "contributor-1", signal, "revision-1")).rejects.toThrow();
});

it("does not send writes for invalid reasons, targets or vote scopes", () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  for (const invalid of [{ ...intent, reason: " " }, { ...intent, reason: "x".repeat(1001) },
    { ...intent, reason: "bad\u0000reason" }, { ...intent, revisionId: "revision-1" },
    { ...intent, command: "exclude_vote" as const }, { ...intent, targetId: "../secret" }])
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