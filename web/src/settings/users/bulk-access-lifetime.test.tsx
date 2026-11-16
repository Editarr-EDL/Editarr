import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { setCsrfToken } from "../../api";
import type { AuthenticationStatus } from "../../api/api-system-types";
import { AuthenticationGate, usePrincipal } from "../../auth/auth-gate";
import { json, signedInStatus } from "../../test/app-test-fixtures";
import { BulkAccess } from "./bulk-access";
import { ordinaryAccount } from "./users.test-support";

const signedOut: AuthenticationStatus = {
  state: "unauthenticated", setup_required: false, sign_in: { jellyfin: true },
  restart_required: false, csrf_token: null, access: null,
};
const targets = [ordinaryAccount, { ...ordinaryAccount, id: 4, display_name: "Second target" }, { ...ordinaryAccount, id: 6, display_name: "Third target" }];
afterEach(() => { cleanup(); setCsrfToken(null); vi.unstubAllGlobals(); window.localStorage.clear(); window.location.hash = ""; });

function renderHeldBatch(method: "GET" | "PUT", response: () => Response) {
  let status = signedInStatus({ userId: 2, role: "admin", revision: 1 });
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const requests: string[] = [];
  let readSignal: AbortSignal | null | undefined;
  const finished = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/status") return json(status);
    const requestMethod = options?.method ?? "GET";
    requests.push(`${requestMethod} ${path}`);
    if (requestMethod === method && path.includes("/users/3")) {
      if (method === "GET") readSignal = options?.signal;
      await pending;
      return response();
    }
    return json({ revision: requestMethod === "PUT" ? 2 : 1 });
  }));
  function Probe() {
    const principal = usePrincipal();
    const [shown, setShown] = useState(true);
    return principal && <>
      <p>Signed-in actor {principal.userId}</p>
      <button onClick={() => setShown(!shown)}>{shown ? "Leave users" : "Return to users"}</button>
      {shown && <BulkAccess selected={targets} principal={principal} onChanged={finished} />}
    </>;
  }
  render(<AuthenticationGate unauthenticated={<p>Signed out view</p>}><Probe /></AuthenticationGate>);
  return {
    requests,
    readSignal: () => readSignal,
    setStatus(next: AuthenticationStatus) { status = next; },
    async confirm() {
      fireEvent.click(await screen.findByRole("button", { name: "Set selected filter modes" }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm selected changes" }));
      await waitFor(() => expect(requests.at(-1)).toBe(`${method} /api/settings/filters/users/3${method === "PUT" ? "/mode" : ""}`));
      return [...requests];
    },
    async release() {
      await act(async () => { release(); await pending; });
      await waitFor(() => expect(finished).toHaveBeenCalledOnce());
    },
  };
}

it.each([
  { transition: "identity replacement", nextUser: 5, nextRevision: 1, heldMethod: "PUT", heldUser: 3, unchanged: false },
  { transition: "permission revision replacement", nextUser: 2, nextRevision: 2, heldMethod: "PUT", heldUser: 3, unchanged: false },
  { transition: "identity replacement during GET", nextUser: 5, nextRevision: 1, heldMethod: "GET", heldUser: 3, unchanged: false },
  { transition: "permission revision replacement during GET", nextUser: 2, nextRevision: 2, heldMethod: "GET", heldUser: 3, unchanged: false },
  { transition: "revision replacement after an observed success", nextUser: 2, nextRevision: 2, heldMethod: "PUT", heldUser: 4, unchanged: false },
  { transition: "unchanged authentication refresh", nextUser: 2, nextRevision: 1, heldMethod: "PUT", heldUser: 3, unchanged: true },
])("respects the confirming lifetime after $transition", async ({ nextUser, nextRevision, heldMethod, heldUser, unchanged }) => {
  let current = signedInStatus({ userId: 2, role: "admin", revision: 1 });
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  const requests: { path: string; method: string; token: string | null; credentials: RequestCredentials | undefined }[] = [];
  let readSignal: AbortSignal | null | undefined;
  const finished = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/status") return json(current);
    const method = options?.method ?? "GET";
    requests.push({ path, method, token: new Headers(options?.headers).get("X-CSRF-Token"), credentials: options?.credentials });
    if (method === heldMethod && path.includes(`/users/${heldUser}`)) {
      readSignal = options?.signal;
      await pending;
    }
    return json({ revision: method === "PUT" ? 2 : 1 });
  }));
  function Probe() {
    const principal = usePrincipal();
    return principal && <><p>Signed-in actor {principal.userId}</p><BulkAccess selected={targets} principal={principal} onChanged={finished} /></>;
  }
  render(<AuthenticationGate><Probe /></AuthenticationGate>);
  fireEvent.click(await screen.findByRole("button", { name: "Set selected filter modes" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm selected changes" }));
  await waitFor(() => expect(requests.filter(request => request.method === heldMethod)).toHaveLength(heldUser === 4 ? 2 : 1));
  const confirmedRequests = [...requests];
  current = { ...signedInStatus({ userId: nextUser, role: "admin", revision: nextRevision }), csrf_token: "B".repeat(43) };
  await act(async () => { fireEvent(window, new Event("focus")); });
  await screen.findByText(`Signed-in actor ${nextUser}`);
  if (!unchanged) await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  if (heldMethod === "GET") expect(readSignal?.aborted).toBe(true);
  await act(async () => { release(); await pending; });
  await waitFor(() => expect(finished).toHaveBeenCalled());
  if (unchanged) {
    expect(requests.filter(request => request.method === "PUT")).toHaveLength(3);
    expect(screen.getByRole("region", { name: "Bulk access results" })).toHaveTextContent("Third target: applied");
    return;
  }
  expect(requests).toEqual(confirmedRequests);
  if (nextUser !== 2) expect(screen.queryByRole("region", { name: "Bulk access results" })).not.toBeInTheDocument();
  else if (heldMethod === "PUT") {
    const results = screen.getByRole("region", { name: "Bulk access results" });
    expect(results).toHaveTextContent("outcome unknown");
    expect(results).not.toHaveTextContent("Third target");
    if (heldUser === 4) expect(results).toHaveTextContent("Pat: applied");
  }
  else expect(screen.queryByRole("region", { name: "Bulk access results" })).not.toBeInTheDocument();
});

it.each([
  { transition: "sign-out", method: "PUT" as const, next: () => signedOut },
  { transition: "sign-out", method: "GET" as const, next: () => signedOut },
])("starts no request after $transition while a $method is held", async ({ transition, method, next }) => {
  const batch = renderHeldBatch(method, () => json({ revision: method === "PUT" ? 2 : 1 }));
  const confirmed = await batch.confirm();
  batch.setStatus(next());
  await act(async () => { fireEvent(window, new Event("focus")); });
  await waitFor(() => expect(screen.queryByText(/Signed-in actor/)).not.toBeInTheDocument());
  if (transition === "sign-out") expect(screen.getByText("Signed out view")).toBeInTheDocument();
  else expect(screen.getByRole("heading", { name: "CHOOSE A NEW PASSWORD" })).toBeInTheDocument();
  if (method === "GET") expect(batch.readSignal()?.aborted).toBe(true);
  await batch.release();
  expect(batch.requests).toEqual(confirmed);
  expect(screen.queryByRole("region", { name: "Bulk access results" })).not.toBeInTheDocument();
});

it("starts no request after the held PUT itself reports session expiry", async () => {
  const batch = renderHeldBatch("PUT", () => json({ code: "not_authenticated", detail: "Sign in" }, 401));
  const confirmed = await batch.confirm();
  batch.setStatus(signedOut);
  await batch.release();
  expect(await screen.findByText("Signed out view")).toBeInTheDocument();
  expect(batch.requests).toEqual(confirmed);
  expect(screen.queryByRole("region", { name: "Bulk access results" })).not.toBeInTheDocument();
});

it("aborts a held GET on ordinary unmount and records no receipt", async () => {
  const batch = renderHeldBatch("GET", () => json({ revision: 1 }));
  const confirmed = await batch.confirm();
  fireEvent.click(screen.getByRole("button", { name: "Leave users" }));
  expect(batch.readSignal()?.aborted).toBe(true);
  await batch.release();
  expect(batch.requests).toEqual(confirmed);
  fireEvent.click(screen.getByRole("button", { name: "Return to users" }));
  expect(screen.queryByRole("region", { name: "Bulk access results" })).not.toBeInTheDocument();
});

it("lets a started PUT finish on ordinary unmount and records it as unknown only", async () => {
  const batch = renderHeldBatch("PUT", () => json({ revision: 2 }));
  const confirmed = await batch.confirm();
  fireEvent.click(screen.getByRole("button", { name: "Leave users" }));
  await batch.release();
  expect(batch.requests).toEqual(confirmed);
  fireEvent.click(screen.getByRole("button", { name: "Return to users" }));
  const results = screen.getByRole("region", { name: "Bulk access results" });
  expect(results).toHaveTextContent("Pat: outcome unknown");
  expect(results).not.toHaveTextContent("applied");
  expect(results).not.toHaveTextContent("Second target");
  expect(results).not.toHaveTextContent("Third target");
});

it("records an abandoned started PUT that later fails as unknown, not refused", async () => {
  const batch = renderHeldBatch("PUT", () => json({ code: "filter_preferences_conflict", detail: "Changed" }, 409));
  const confirmed = await batch.confirm();
  batch.setStatus({ ...signedInStatus({ userId: 2, role: "admin", revision: 2 }), csrf_token: "B".repeat(43) });
  await act(async () => { fireEvent(window, new Event("focus")); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await batch.release();
  expect(batch.requests).toEqual(confirmed);
  const results = await screen.findByRole("region", { name: "Bulk access results" });
  expect(results).toHaveTextContent("Pat: outcome unknown");
  expect(results).not.toHaveTextContent("refused");
  expect(results).not.toHaveTextContent("Second target");
  expect(results).not.toHaveTextContent("Third target");
});

it("keeps a known read refusal observed after a same-user revision change", async () => {
  const batch = renderHeldBatch("GET", () => json({ code: "protected_account", detail: "Protected" }, 403));
  const confirmed = await batch.confirm();
  batch.setStatus({ ...signedInStatus({ userId: 2, role: "admin", revision: 2 }), csrf_token: "B".repeat(43) });
  await act(async () => { fireEvent(window, new Event("focus")); });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await batch.release();
  expect(batch.requests).toEqual(confirmed);
  const results = await screen.findByRole("region", { name: "Bulk access results" });
  expect(results).toHaveTextContent("Pat: refused (protected_account)");
  expect(results).not.toHaveTextContent("outcome unknown");
  expect(results).not.toHaveTextContent("Second target");
  expect(results).not.toHaveTextContent("Third target");
});
