import { afterEach, describe, expect, it, vi } from "vitest";
import { bulkAccountStatus, listAccounts, uploadAccountAvatar } from "./api-users";
import { importJellyfinAccounts } from "./api-jellyfin-accounts";
import { setCsrfToken } from "./api-transport";

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });

describe("account requests", () => {
  it("escapes directory search and uses bounded server paging", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [] })));
    vi.stubGlobal("fetch", fetch);
    await listAccounts("A&B", 20);
    const [url, init] = fetch.mock.calls[0];
    expect(new URL(url, "http://localhost").searchParams.get("search")).toBe("A&B");
    expect(url).toContain("offset=20&limit=20");
    expect(init.cache).toBe("no-store");
  });

  it("sends CSRF with bulk status changes and keeps per-target results", async () => {
    setCsrfToken("test-csrf");
    const results = [{ user_id: 2, outcome: "applied", code: null }, { user_id: 1, outcome: "refused", code: "protected_account" }];
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ results }))));
    vi.stubGlobal("fetch", fetch);
    expect(await bulkAccountStatus([2, 1], "disabled")).toEqual({ results });
    expect(fetch.mock.calls[0][1].headers.get("X-CSRF-Token")).toBe("test-csrf");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ action: "disable", user_ids: [2, 1] });
  });

  it("sends the raw avatar and distinct selected/all import bodies", async () => {
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(new Response("{}")));
    vi.stubGlobal("fetch", fetch);
    const file = new File(["image"], "avatar.png", { type: "image/png" });
    await uploadAccountAvatar(2, file);
    expect(fetch.mock.calls[0][1].body).toBe(file);
    expect(fetch.mock.calls[0][1].headers.get("Content-Type")).toBe("image/png");
    await importJellyfinAccounts({ all: true });
    await importJellyfinAccounts({ user_ids: ["guid"] });
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ all: true });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ user_ids: ["guid"] });
  });
});