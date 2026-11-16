import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken, subscribeToAuthenticationState } from "../api";
import { RestartEditarr, openRestartTarget } from "./restart-editarr";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function capability(overrides: Record<string, unknown> = {}) {
  return {
    supported: true,
    mode: "standalone_controller",
    generation: 2,
    in_progress: false,
    outcome: "applied",
    target_generation: 2,
    reason: null,
    detail: null,
    ...overrides,
  };
}

function accepted(reconnectUrl: string, targetGeneration = 2) {
  return json({ status: "accepted", target_generation: targetGeneration, reconnect_url: reconnectUrl }, 202);
}

async function advancePolling(milliseconds = 20) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

describe("Restart Editarr", () => {
  afterEach(() => {
    cleanup();
    setCsrfToken(null);
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("labels the action exactly and distinguishes clean from dirty settings", () => {
    const { rerender } = render(<RestartEditarr hasUnsavedChanges={false} />);
    expect(screen.getByRole("button", { name: "Restart Editarr" })).toBeEnabled();
    expect(screen.getByText(/still restarts Editarr when no changes are pending/)).toBeVisible();

    rerender(<RestartEditarr hasUnsavedChanges />);
    expect(screen.getByText(/You have unsaved changes/)).toHaveTextContent("these edits will not be applied");
  });

  it("sends one protected restart request and waits for an applied target generation", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/#/settings/general`;
    let finishRequest: (response: Response) => void = () => undefined;
    const pendingRequest = new Promise<Response>((resolve) => { finishRequest = resolve; });
    const request = vi.fn()
      .mockImplementationOnce(() => pendingRequest)
      .mockResolvedValueOnce(json(capability({ generation: 1, outcome: "idle", target_generation: null })))
      .mockResolvedValueOnce(json(capability()));
    const navigate = vi.fn();
    vi.stubGlobal("fetch", request);
    setCsrfToken("csrf-token");
    render(<StrictMode><RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={3} navigate={navigate} /></StrictMode>);

    const button = screen.getByRole("button", { name: "Restart Editarr" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(request).toHaveBeenCalledTimes(1);
    expect(button).toBeDisabled();
    expect(new Headers((request.mock.calls[0][1] as RequestInit).headers).get("X-CSRF-Token")).toBe("csrf-token");

    finishRequest(accepted(target));
    await advancePolling();

    expect(request.mock.calls.filter(([, init]) => (init as RequestInit).method === "POST")).toHaveLength(1);
    expect(navigate).toHaveBeenCalledWith(target);
  });

  it("permits an independent restart only after a fully observed applied result", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/#/settings/general`;
    const request = vi.fn()
      .mockResolvedValueOnce(accepted(target, 2))
      .mockResolvedValueOnce(json(capability({ generation: 2, target_generation: 2 })))
      .mockResolvedValueOnce(accepted(target, 3))
      .mockResolvedValueOnce(json(capability({ generation: 3, target_generation: 3 })));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={2} navigate={vi.fn()} />);

    const button = screen.getByRole("button", { name: "Restart Editarr" });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    await advancePolling();
    expect(button).toBeEnabled();

    fireEvent.click(button);
    await advancePolling();
    expect(request.mock.calls.filter(([, init]) => (init as RequestInit).method === "POST")).toHaveLength(2);
  });

  it("does not accept an old generation and times out with the server target", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/#/settings/general`;
    const request = vi.fn()
      .mockResolvedValueOnce(accepted(target, 5))
      .mockResolvedValue(json(capability({ generation: 4, outcome: "applied", target_generation: 5 })));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={2} navigate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling();

    expect(screen.getByRole("alert")).toHaveTextContent("did not become ready in time");
    expect(screen.getByRole("link", { name: "Open restart target" })).toHaveAttribute("href", target);
  });

  it("finds recovery on the current endpoint when the saved prefix target is unavailable", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/new-prefix/#/settings/general`;
    let currentPoll = 0;
    const request = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin);
      if (init?.method === "POST") return accepted(target);
      if (url.pathname === "/new-prefix/api/system/restart") return json({ detail: "not found" }, 404);
      if (url.pathname === "/api/system/restart") {
        currentPoll += 1;
        return json(currentPoll === 1
          ? capability({ generation: 1, outcome: "applied", in_progress: false })
          : capability({ generation: 2, outcome: "recovered", detail: "Port 8123 could not bind; recovered on port 8000" }));
      }
      throw new Error(`Unexpected URL ${url.href}`);
    });
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={3} navigate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling();

    expect(screen.getByRole("alert")).toHaveTextContent("recovered on port 8000");
    expect(screen.getByRole("button", { name: "Restart Editarr" })).toBeEnabled();
  });

  it("never polls a cross-origin target but surfaces recovery from the current origin", async () => {
    vi.useFakeTimers();
    const target = "https://editarr.example.com/media/editarr/#/settings/general";
    const request = vi.fn()
      .mockResolvedValueOnce(accepted(target))
      .mockResolvedValueOnce(json(capability({ generation: 2, outcome: "recovered", detail: "Recovered on the previous listener" })));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={2} navigate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByRole("link", { name: "Open restart target" })).toHaveAttribute("href", target);
    await advancePolling();

    expect(request.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/system/restart",
      `${window.location.origin}/api/system/restart`,
    ]);
    expect(screen.getByRole("alert")).toHaveTextContent("Recovered on the previous listener");
  });

  it("uses the server reconnect URL for a cross-origin CLI-port override flow", async () => {
    vi.useFakeTimers();
    const target = "https://public.example:444/editarr/#/settings/general";
    const request = vi.fn()
      .mockResolvedValueOnce(accepted(target))
      .mockRejectedValue(new TypeError("service stopped"));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={1} navigate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await act(async () => { await Promise.resolve(); });
    const link = screen.getByRole("link", { name: "Open restart target" });
    expect(link).toHaveAttribute("href", target);
    expect(link.getAttribute("href")).not.toContain("9000");
    await advancePolling();
  });

  it("reports unsupported and rejected restart responses with actionable detail", async () => {
    const request = vi.fn().mockResolvedValue(json({
      code: "restart_unsupported",
      detail: "Restart Editarr requires the standalone editarr launcher",
    }, 409));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("standalone editarr launcher");
    expect(screen.getByRole("button", { name: "Restart Editarr" })).toBeEnabled();
  });

  it("shows controller request failures and permits a retry", async () => {
    const request = vi.fn().mockResolvedValue(json({
      code: "restart_unavailable",
      detail: "The standalone controller is unavailable",
    }, 503));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("controller is unavailable");
    expect(screen.getByRole("button", { name: "Restart Editarr" })).toBeEnabled();
  });

  it("navigates through the auth gate when target polling reports session expiry", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/new-prefix/#/settings/general`;
    const navigate = vi.fn();
    const authenticationStates: string[] = [];
    const unsubscribe = subscribeToAuthenticationState((state) => authenticationStates.push(state));
    const request = vi.fn()
      .mockResolvedValueOnce(accepted(target))
      .mockResolvedValueOnce(json({ code: "authentication_required", detail: "Sign in again" }, 401));
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} maxPollAttempts={1} navigate={navigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling();

    expect(navigate).toHaveBeenCalledWith(target);
    expect(authenticationStates).toEqual([]);
    unsubscribe();
  });

  it("times out a stalled target and still checks the previous endpoint for recovery", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/new-prefix/#/settings/general`;
    let targetSignal: AbortSignal | undefined;
    const request = vi.fn()
      .mockResolvedValueOnce(accepted(target))
      .mockImplementationOnce((_, init: RequestInit) => {
        targetSignal = init.signal!;
        return new Promise<Response>(() => {});
      })
      .mockResolvedValueOnce(json(capability({ outcome: "recovered", detail: "Recovered on previous listener" })));
    const navigate = vi.fn();
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} requestTimeoutMs={5} maxPollAttempts={2} navigate={navigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling();

    expect(targetSignal?.aborted).toBe(true);
    expect(request.mock.calls.map(([url]) => String(url))).toEqual([
      "/api/system/restart", `${window.location.origin}/new-prefix/api/system/restart`, `${window.location.origin}/api/system/restart`,
    ]);
    expect(screen.getByRole("alert")).toHaveTextContent("Recovered on previous listener");
    expect(navigate).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("bounds stalled target response bodies and stalled recovery requests", async () => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/new-prefix/#/settings/general`;
    const signals: AbortSignal[] = [];
    const request = vi.fn(async (input: string, init: RequestInit) => {
      if (init.method === "POST") return accepted(target);
      signals.push(init.signal!);
      if (input.includes("new-prefix")) {
        return { ok: true, json: () => new Promise(() => {}) } as Response;
      }
      return new Promise<Response>(() => {});
    });
    const navigate = vi.fn();
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} requestTimeoutMs={5} maxPollAttempts={2} navigate={navigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling(30);

    expect(signals).toHaveLength(4);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent("did not become ready in time");
    expect(screen.getByRole("link", { name: "Open restart target" })).toHaveAttribute("href", target);
    expect(navigate).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("treats a lost POST acknowledgment as uncertain without sending a duplicate or accepting a late response", async () => {
    vi.useFakeTimers();
    let finishRequest: (response: Response) => void = () => {};
    const request = vi.fn(() => new Promise<Response>((resolve) => { finishRequest = resolve; }));
    const navigate = vi.fn();
    vi.stubGlobal("fetch", request);
    render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={1} requestTimeoutMs={5} navigate={navigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling();

    expect(screen.getByRole("alert")).toHaveTextContent("Editarr may already be restarting; no repeat request was sent");
    const button = screen.getByRole("button", { name: "Restart Editarr" });
    expect(button).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reload General Settings" })).toBeEnabled();
    fireEvent.click(button);
    finishRequest(accepted(`${window.location.origin}/#/settings/general`));
    await advancePolling();
    expect(request).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(["request", "poll", "delay"])("cancels the active %s and all timers when unmounted", async (stage) => {
    vi.useFakeTimers();
    const target = `${window.location.origin}/#/settings/general`;
    let signal: AbortSignal | null = null;
    let finishRequest: (response: Response) => void = () => {};
    const request = vi.fn((_: string, init: RequestInit) => {
      signal = init.signal!;
      if (stage !== "request" && init.method === "POST") return Promise.resolve(accepted(target));
      return new Promise<Response>((resolve) => { finishRequest = resolve; });
    });
    const navigate = vi.fn();
    vi.stubGlobal("fetch", request);
    const { unmount } = render(<RestartEditarr hasUnsavedChanges={false} pollIntervalMs={stage === "delay" ? 100 : 1} requestTimeoutMs={100} navigate={navigate} />);

    fireEvent.click(screen.getByRole("button", { name: "Restart Editarr" }));
    await advancePolling(2);
    const callsBeforeUnmount = request.mock.calls.length;
    unmount();
    await advancePolling(200);
    if (stage !== "delay") expect((signal as AbortSignal | null)?.aborted).toBe(true);
    finishRequest(stage === "request" ? accepted(target) : json(capability()));
    await advancePolling(200);

    expect(request).toHaveBeenCalledTimes(callsBeforeUnmount);
    expect(navigate).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("openRestartTarget", () => {
  function browserLocation(href: string) {
    const visits: string[] = [];
    return {
      visits,
      location: {
        href,
        assign: (url: string) => { visits.push(`assign ${url}`); },
        reload: () => { visits.push("reload"); },
      },
    };
  }

  it("loads a fresh document when the restart target is the current page", () => {
    const { visits, location } = browserLocation("https://editarr.example/editarr/#/settings/general");

    openRestartTarget("https://editarr.example/editarr/#/settings/general", location);

    expect(visits).toEqual(["assign https://editarr.example/editarr/#/settings/general", "reload"]);
  });

  it("loads a fresh document when only the fragment differs", () => {
    const { visits, location } = browserLocation("https://editarr.example/editarr/#/settings/connections");

    openRestartTarget("https://editarr.example/editarr/#/settings/general", location);

    expect(visits).toEqual(["assign https://editarr.example/editarr/#/settings/general", "reload"]);
  });

  it("leaves a changed path to the cross-document navigation alone", () => {
    const { visits, location } = browserLocation("https://editarr.example/#/settings/general");

    openRestartTarget("https://editarr.example/editarr/#/settings/general", location);

    expect(visits).toEqual(["assign https://editarr.example/editarr/#/settings/general"]);
  });
});
