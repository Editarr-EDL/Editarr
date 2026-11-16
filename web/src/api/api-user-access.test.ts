import { afterEach, describe, expect, it, vi } from "vitest";
import { assignAccountRole, readPermissions, saveFilterMode, watchAccountAccess } from "./api-user-access";
import { setCsrfToken } from "./api-transport";

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });
describe("Account-policy client boundary", () => {
  it("sends role proof in the JSON body and never the URL", async () => {
    setCsrfToken("synthetic-csrf");
    const fetch = vi.fn().mockResolvedValue(new Response("{}")); vi.stubGlobal("fetch", fetch);
    await assignAccountRole(8, "admin", "synthetic proof");
    expect(fetch.mock.calls[0][0]).toBe("/api/users/8/role");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ role: "admin", reauthentication: { password: "synthetic proof" } });
    expect(fetch.mock.calls[0][1].headers.get("X-CSRF-Token")).toBe("synthetic-csrf");
  });
  it("notifies only the editor that owned the denied request", async () => {
    let respond!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => new Promise(resolve => { respond = resolve; })));
    const old = vi.fn(), current = vi.fn(); const stop = watchAccountAccess(8, old);
    const request = readPermissions(8); stop(); const stopCurrent = watchAccountAccess(8, current);
    respond(new Response(JSON.stringify({ code: "protected_account", detail: "Protected" }), { status: 403 }));
    await expect(request).rejects.toThrow(); expect(old).not.toHaveBeenCalled(); expect(current).not.toHaveBeenCalled(); stopCurrent();
  });
  it("clears an active editor on filter target denial but not rejected fresh proof", async () => {
    const denied = vi.fn(); const stop = watchAccountAccess(8, denied);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: "reauthentication_failed" }), { status: 403 })).mockResolvedValueOnce(new Response(JSON.stringify({ code: "protected_account" }), { status: 403 })));
    await expect(assignAccountRole(8, "user", "wrong")).rejects.toThrow(); expect(denied).not.toHaveBeenCalled();
    await expect(saveFilterMode(8, 2, "live")).rejects.toThrow(); expect(denied).toHaveBeenCalledTimes(1); stop();
  });
});