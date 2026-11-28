import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { OperatorContext, type OperatorServices } from "./shared/operator-data";
import { operatorFixture } from "./test-fixtures";
import { now } from "./test-fixtures-insights";

/**
 * Stub `fetch` with the synthetic operator fixtures; `handle` may answer a request first (return a Response, a pending
 * Response promise or a JSON
 * value) and returns undefined to fall through. Returns the mock so tests can inspect calls.
 */
export function stubOperatorFetch(handle?: (path: string, init?: RequestInit) => unknown) {
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    const custom = handle?.(path, init);
    if (custom instanceof Response) return Promise.resolve(custom);
    if (custom instanceof Promise) return custom as Promise<Response>;
    if (custom !== undefined) return Promise.resolve(Response.json(custom));
    try { return Promise.resolve(Response.json(operatorFixture(path))); }
    catch { return Promise.resolve(Response.json({ error: { code: "resource_not_found" } }, { status: 404 })); }
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
/** Render operator UI inside the app services context; returns the services' spies. */
export function renderOperator(ui: ReactNode, services: Partial<OperatorServices> = {}) {
  const value: OperatorServices = { subject: "ryan@example.org", counts: null, onAccessFailure: vi.fn(), refreshCounts: vi.fn(), setObservedAt: vi.fn(), ...services };
  return { ...render(<OperatorContext.Provider value={value}>{ui}</OperatorContext.Provider>), services: value };
}
/** Make `HTMLDialogElement.showModal` work in jsdom. */
export function enableDialogs() {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
}
/**
 * Pin the clock to the fixture time so relative ages ("15 d", "2 h ago") never depend on the real date; time still advances.
 * Pair with `vi.useRealTimers()` in cleanup. Pass `timers` to fake timers too (for polling and debounce tests).
 */
export function pinFixtureClock(timers = false) {
  vi.useFakeTimers({ now, shouldAdvanceTime: true, ...(timers ? {} : { toFake: ["Date"] }) });
}
