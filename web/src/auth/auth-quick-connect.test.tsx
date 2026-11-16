import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "../api";
import type { AuthenticationStatus } from "../api/api-system-types";
import { signedInStatus } from "../test/app-test-fixtures";
import { AuthenticationGate } from "./auth-gate";
import { AdministratorLogin } from "./auth-login";
import { QUICK_CONNECT_POLL_MS } from "./auth-quick-connect";

const QC = "/api/auth/jellyfin/quick-connect";
const signedOut: AuthenticationStatus = { state: "unauthenticated", setup_required: false, sign_in: { jellyfin: true }, restart_required: false };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/**
 * A fetch double that answers each `METHOD path` from its own queue, repeating the last answer.
 * An `Error` in a queue is thrown, as fetch does when the server cannot be reached.
 */
function jellyfinServer(routes: Record<string, unknown[]>) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(input)}`;
    const queue = routes[key];
    if (!queue) throw new Error(`Unexpected ${key}`);
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next instanceof Error) throw next;
    return next instanceof Response ? next.clone() : json(next);
  });
  vi.stubGlobal("fetch", fetch);
  return { calls: (key: string) => fetch.mock.calls.filter(([input, init]) => `${init?.method ?? "GET"} ${String(input)}` === key) };
}

function renderLogin() {
  return render(
    <StrictMode>
      <AuthenticationGate unauthenticated={<AdministratorLogin />}>
        <h1>Protected application</h1>
      </AuthenticationGate>
    </StrictMode>,
  );
}

async function startQuickConnect() {
  fireEvent.click(await screen.findByRole("button", { name: "Use Quick Connect" }));
  vi.useFakeTimers();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
}

async function poll() {
  await advance(QUICK_CONNECT_POLL_MS);
}

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

const pending = { state: "pending", code: "894255" };
const proxyError = (status: number) => new Response("Bad gateway", { status, headers: { "Content-Type": "text/plain" } });

afterEach(() => {
  cleanup();
  setCsrfToken(null);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Quick Connect sign-in", () => {
  it("shows the code, polls, and signs in once Jellyfin approves it", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json({ state: "pending", code: "894255" }, 201)],
      [`GET ${QC}/status`]: [{ state: "pending", code: "894255" }, { state: "approved", code: "894255" }],
      [`POST ${QC}/complete`]: [{ ...signedInStatus(), csrf_token: "csrf-token" }],
    });
    renderLogin();
    fireEvent.click(await screen.findByLabelText("Remember me"));
    await startQuickConnect();

    expect(screen.getByLabelText("Quick Connect code")).toHaveTextContent("894255");
    expect(screen.getByLabelText("Quick Connect code")).toHaveFocus();
    expect(screen.getByText("Only approve a code you started here.")).toBeInTheDocument();
    expect(screen.getByText("Waiting for approval…")).toHaveAttribute("role", "status");
    await poll();
    expect(screen.getByLabelText("Quick Connect code")).toBeInTheDocument();
    await poll();

    expect(screen.getByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(server.calls(`POST ${QC}/initiate`)).toHaveLength(1);
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(2);
    const [[, complete]] = server.calls(`POST ${QC}/complete`);
    expect(JSON.parse(String(complete!.body))).toEqual({ remember_me: false });
  });

  it("announces that it is getting a code in the region that later reports approval", async () => {
    let answer: (response: Response) => void = () => undefined;
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      if (key === "GET /api/auth/status") return json(signedOut);
      if (key === `GET ${QC}`) return json({ enabled: true });
      if (key === `POST ${QC}/initiate`) return await new Promise<Response>((resolve) => { answer = resolve; });
      throw new Error(`Unexpected ${key}`);
    });
    vi.stubGlobal("fetch", fetch);
    renderLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Use Quick Connect" }));

    const status = await screen.findByText("Getting a code from Jellyfin…");
    expect(status).toHaveAttribute("role", "status");
    await act(async () => { answer(json({ state: "pending", code: "894255" }, 201)); });
    expect(screen.getByText("Waiting for approval…")).toBe(status);
  });

  it("is not offered when Jellyfin has Quick Connect off", async () => {
    jellyfinServer({ "GET /api/auth/status": [signedOut], [`GET ${QC}`]: [{ enabled: false }] });
    renderLogin();

    expect(await screen.findByLabelText("Jellyfin username")).toBeInTheDocument();
    await act(async () => {});
    expect(screen.queryByRole("button", { name: "Use Quick Connect" })).not.toBeInTheDocument();
  });

  it("stops at an expired code and offers a new one", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json({ state: "pending", code: "111111" }, 201), json({ state: "pending", code: "222222" }, 201)],
      [`GET ${QC}/status`]: [{ state: "expired", code: "111111" }, { state: "pending", code: "222222" }],
    });
    renderLogin();
    await startQuickConnect();
    await poll();

    expect(screen.getByRole("alert")).toHaveTextContent("This code expired.");
    expect(screen.queryByLabelText("Quick Connect code")).not.toBeInTheDocument();
    await poll();
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Get a new code" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByLabelText("Quick Connect code")).toHaveTextContent("222222");
  });

  it("shows a code Editarr forgot, as after a restart, as expired", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json({ state: "pending", code: "111111" }, 201)],
      [`GET ${QC}/status`]: [json({ code: "quick_connect_not_found", detail: "No Quick Connect sign-in is pending" }, 404)],
    });
    renderLogin();
    await startQuickConnect();
    await poll();
    await poll();

    expect(screen.getByRole("alert")).toHaveTextContent("This code expired.");
    expect(screen.getByRole("button", { name: "Get a new code" })).toBeInTheDocument();
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(1);
  });

  it("cancels on the server, stops polling, and returns to the password form", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json({ state: "pending", code: "894255" }, 201)],
      [`GET ${QC}/status`]: [{ state: "pending", code: "894255" }],
      [`POST ${QC}/cancel`]: [new Response(null, { status: 204 })],
    });
    renderLogin();
    await startQuickConnect();
    await poll();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await poll();
    await poll();

    expect(server.calls(`POST ${QC}/cancel`)).toHaveLength(1);
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(1);
    expect(screen.getByLabelText("Jellyfin password")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
  });

  it("rides out an Editarr restart and then shows the forgotten code as expired", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json(pending, 201)],
      [`GET ${QC}/status`]: [
        pending,
        proxyError(500),
        new TypeError("Failed to fetch"),
        proxyError(502),
        json({ code: "quick_connect_not_found", detail: "No Quick Connect sign-in is pending" }, 404),
      ],
    });
    renderLogin();
    await startQuickConnect();
    await poll();
    await poll();

    expect(screen.getByLabelText("Quick Connect code")).toHaveTextContent("894255");
    expect(screen.getByText("Editarr could not be reached. Still trying…")).toHaveAttribute("role", "status");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await advance(1000 + 2000 + 4000);

    expect(screen.getByRole("alert")).toHaveTextContent("This code expired. Get a new code to try again.");
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(5);
    await advance(30_000);
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(5);
  });

  it("backs off while Editarr is unreachable and stops after about thirty seconds", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json(pending, 201)],
      [`GET ${QC}/status`]: [proxyError(503)],
    });
    renderLogin();
    await startQuickConnect();
    await poll();
    await advance(7_500);

    expect(server.calls(`GET ${QC}/status`)).toHaveLength(4);
    expect(screen.getByLabelText("Quick Connect code")).toBeInTheDocument();
    await advance(16_000);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await advance(8_000);

    expect(screen.getByRole("alert")).toHaveTextContent("Editarr could not be reached, so Quick Connect stopped.");
    expect(screen.queryByLabelText("Quick Connect code")).not.toBeInTheDocument();
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(7);
    await advance(30_000);
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(7);
  });

  it("keeps waiting through a Jellyfin outage and reports it once the outage lasts", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json(pending, 201)],
      [`GET ${QC}/status`]: [json({ code: "jellyfin_unavailable", detail: "Jellyfin could not be reached" }, 503)],
    });
    renderLogin();
    await startQuickConnect();
    await poll();

    expect(screen.getByText("Jellyfin could not be reached. Still trying…")).toHaveAttribute("role", "status");
    await advance(31_000);

    expect(screen.getByRole("alert")).toHaveTextContent("Jellyfin could not be reached, so Quick Connect stopped.");
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(7);
  });

  it("recovers from a brief outage and still signs in", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json(pending, 201)],
      [`GET ${QC}/status`]: [
        json({ code: "quick_connect_busy", detail: "Busy" }, 503),
        pending,
        { state: "approved", code: "894255" },
      ],
      [`POST ${QC}/complete`]: [{ ...signedInStatus(), csrf_token: "csrf-token" }],
    });
    renderLogin();
    await startQuickConnect();
    await poll();
    await poll();

    expect(screen.getByText("Waiting for approval…")).toHaveAttribute("role", "status");
    await poll();
    expect(screen.getByRole("heading", { name: "Protected application" })).toBeInTheDocument();
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(3);
  });

  it("waits as long as Retry-After asks before checking again", async () => {
    const server = jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json(pending, 201)],
      [`GET ${QC}/status`]: [
        new Response(JSON.stringify({ code: "rate_limited", detail: "Slow down" }), { status: 429, headers: { "Retry-After": "5" } }),
        pending,
      ],
    });
    renderLogin();
    await startQuickConnect();
    await poll();

    expect(screen.getByText("Checking again in 5 seconds.")).toHaveAttribute("role", "status");
    expect(screen.getByLabelText("Quick Connect code")).toBeInTheDocument();
    await advance(4_900);
    expect(server.calls(`GET ${QC}/status`)).toHaveLength(1);
    await advance(100);

    expect(server.calls(`GET ${QC}/status`)).toHaveLength(2);
    expect(screen.getByText("Waiting for approval…")).toHaveAttribute("role", "status");
  });

  it("shows how long to wait when too many codes were requested", async () => {
    jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [
        new Response(JSON.stringify({ code: "quick_connect_throttled", detail: "Too many" }), { status: 429, headers: { "Retry-After": "30" } }),
        json(pending, 201),
      ],
    });
    renderLogin();
    await startQuickConnect();

    expect(screen.getByRole("alert")).toHaveTextContent("Too many Quick Connect codes were requested. Try again in 30 seconds.");
    expect(screen.getByRole("button", { name: "Get a new code" })).toBeDisabled();
    await advance(30_000);

    expect(screen.getByRole("button", { name: "Get a new code" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Get a new code" }));
    await advance(0);
    expect(screen.getByLabelText("Quick Connect code")).toHaveTextContent("894255");
  });

  it("explains when too many codes are already waiting", async () => {
    jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json({ code: "quick_connect_busy", detail: "Busy" }, 503)],
    });
    renderLogin();
    await startQuickConnect();

    expect(screen.getByRole("alert")).toHaveTextContent("Too many Quick Connect codes are waiting.");
  });

  it("refuses a disabled account with the password sign-in message", async () => {
    jellyfinServer({
      "GET /api/auth/status": [signedOut],
      [`GET ${QC}`]: [{ enabled: true }],
      [`POST ${QC}/initiate`]: [json({ state: "pending", code: "894255" }, 201)],
      [`GET ${QC}/status`]: [{ state: "approved", code: "894255" }],
      [`POST ${QC}/complete`]: [json({ code: "account_disabled", detail: "This account is disabled" }, 403)],
    });
    renderLogin();
    await startQuickConnect();
    await poll();

    expect(screen.getByRole("alert")).toHaveTextContent("This account is disabled. Ask an administrator for help.");
    expect(screen.queryByRole("heading", { name: "Protected application" })).not.toBeInTheDocument();
  });
});
