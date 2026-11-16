import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { browserFailures } from "./identity-browser-failures.mjs";

const origin = "http://127.0.0.1:9200";
const UNAUTHENTICATED = JSON.stringify({ code: "authentication_required", detail: "Authentication is required" });

function page() {
  const events = new EventEmitter();
  const failed = new WeakMap();
  const actor = { label: "dana", page: { on: (name, listener) => events.on(name, listener) } };
  const send = (method, path, session) => {
    const request = { method: () => method, url: () => origin + path, failure: () => failed.get(request) ?? null, allHeaders: async () => (session ? { cookie: `theme=dark; editarr_session=${session}` } : {}) };
    events.emit("request", request);
    return request;
  };
  const answer = (request, status, body = "{}") => events.emit("response", { status: () => status, url: () => request.url(), request: () => request, text: async () => { if (body === null) throw new Error("response.text: No data found for resource with given identifier"); return body; } });
  const fail = (request, errorText) => { failed.set(request, { errorText }); events.emit("requestfailed", request); };
  return { actor, send, answer, fail };
}

async function outcome(drive, allowances = []) {
  const failures = browserFailures();
  const browser = page();
  failures.watch(browser.actor);
  drive(browser);
  await failures.settled();
  return failures.outcome(allowances, []);
}

const sessions = result => [...result.expected, ...result.unexpected].map(entry => [entry.path, entry.session, Boolean(entry.expected)]);

test("a request in flight while the journey signs its session out may answer 401", async () => {
  const result = await outcome(({ send, answer }) => {
    const status = send("GET", "/api/community/connection/status", "dana-1");
    const logout = send("POST", "/api/auth/logout", "dana-1");
    answer(status, 401, UNAUTHENTICATED);
    answer(logout, 200);
  });
  assert.deepEqual(sessions(result), [["/api/community/connection/status", "signed-out", true]]);
});

test("a 401 without a body is expected only in flight while the journey signs its session out", async () => {
  const result = await outcome(({ send, answer }) => {
    const status = send("GET", "/api/community/connection/status", "owner-1");
    const libraries = send("GET", "/api/libraries", "owner-1");
    answer(libraries, 401, null);
    const logout = send("POST", "/api/auth/logout", "owner-1");
    answer(status, 401, null);
    answer(logout, 200);
  });
  assert.deepEqual(sessions(result), [["/api/community/connection/status", "signed-out", true], ["/api/libraries", "live", false]]);
  assert.deepEqual([...result.expected, ...result.unexpected].map(entry => entry.bodyRead), [false, false]);
});

test("a 401 on a session nobody signed out is unexpected", async () => {
  const result = await outcome(({ send, answer }) => {
    const libraries = send("GET", "/api/libraries", "dana-1");
    send("POST", "/api/auth/logout", "casey-1");
    answer(libraries, 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/libraries", "live", false]]);
});

test("a 401 that arrives before the journey's sign-out is issued is unexpected", async () => {
  const result = await outcome(({ send, answer }) => {
    const status = send("GET", "/api/community/connection/status", "dana-1");
    answer(status, 401, UNAUTHENTICATED);
    answer(send("POST", "/api/auth/logout", "dana-1"), 200);
  });
  assert.deepEqual(sessions(result), [["/api/community/connection/status", "live", false]]);
});

test("a request sent with the old cookie after the sign-out was answered is unexpected", async () => {
  const result = await outcome(({ send, answer }) => {
    answer(send("POST", "/api/auth/logout", "dana-1"), 200);
    answer(send("GET", "/api/libraries", "dana-1"), 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/libraries", "live", false]]);
});

test("a sign-out refused for its own session is unexpected", async () => {
  const result = await outcome(({ send, answer }) => answer(send("POST", "/api/auth/logout", "dana-1"), 401, UNAUTHENTICATED));
  assert.deepEqual(sessions(result), [["/api/auth/logout", "live", false]]);
});

test("a request without a session is a signed-out shell probe", async () => {
  const result = await outcome(({ send, answer }) => answer(send("GET", "/api/auth/api-key", null), 401, UNAUTHENTICATED));
  assert.deepEqual(result.expected.map(entry => [entry.session, entry.expected]), [["none", "signed-out shell probe"]]);
});

test("other failures need a matching allowance, and a 404 must be not_found", async () => {
  const notFound = JSON.stringify({ code: "not_found", detail: "Not found" });
  const allowances = [{ label: "dana", path: "/api/reviews/r1", status: 404, reason: "foreign review", detail: null }];
  const result = await outcome(({ send, answer }) => {
    answer(send("GET", "/api/reviews/r1/filters/f1/mute-preview", "dana-1"), 404, notFound);
    answer(send("GET", "/api/reviews/r1", "dana-1"), 404, JSON.stringify({ detail: "gone" }));
    answer(send("GET", "/api/libraries", "dana-1"), 502, JSON.stringify({ code: "bad_gateway" }));
    answer(send("GET", "/api/libraries", "dana-1"), 200);
  }, allowances);
  assert.deepEqual(result.expected.map(entry => [entry.path, entry.expected]), [["/api/reviews/r1/filters/f1/mute-preview", "foreign review"]]);
  assert.deepEqual(result.unexpected.map(entry => [entry.path, entry.status]), [["/api/reviews/r1", 404], ["/api/libraries", 502]]);
});

test("a request without a session on a page that holds a live one is unexpected", async () => {
  const result = await outcome(({ send, answer }) => {
    answer(send("GET", "/api/libraries", "dana-1"), 200);
    answer(send("GET", "/api/libraries", null), 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/libraries", "dropped", false]]);
});

test("a request without a session after the page's sign-out was answered is a shell probe", async () => {
  const result = await outcome(({ send, answer }) => {
    answer(send("GET", "/api/libraries", "dana-1"), 200);
    answer(send("POST", "/api/auth/logout", "dana-1"), 200);
    answer(send("GET", "/api/auth/api-key", null), 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/auth/api-key", "none", true]]);
});

test("a request without a session reported before its sign-out's answer is a shell probe once that answer ends the session", async () => {
  const result = await outcome(({ send, answer }) => {
    answer(send("GET", "/api/libraries", "alice-1"), 200);
    const logout = send("POST", "/api/auth/logout", "alice-1");
    const probe = send("GET", "/api/auth/api-key", null);
    answer(logout, 200);
    answer(probe, 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/auth/api-key", "none", true]]);
});

test("a request without a session while the page's sign-out is refused is unexpected", async () => {
  const result = await outcome(({ send, answer }) => {
    answer(send("GET", "/api/libraries", "alice-1"), 200);
    const logout = send("POST", "/api/auth/logout", "alice-1");
    const probe = send("GET", "/api/auth/api-key", null);
    answer(logout, 403, JSON.stringify({ code: "csrf_failed" }));
    answer(probe, 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/auth/logout", "live", false], ["/api/auth/api-key", "dropped", false]]);
});

test("a sign-out that fails at the network layer is unexpected and excuses no later 401", async () => {
  const result = await outcome(({ send, answer, fail }) => {
    const logout = send("POST", "/api/auth/logout", "dana-1");
    fail(logout, "net::ERR_CONNECTION_RESET");
    for (let index = 0; index < 50; index++) answer(send("GET", "/api/auth/status", "dana-1"), 200);
    answer(send("GET", "/api/libraries", "dana-1"), 401, UNAUTHENTICATED);
  });
  assert.deepEqual(result.unexpected.map(entry => [entry.path, entry.status, entry.session]), [["/api/libraries", 401, "live"], ["/api/auth/logout", null, "n/a"]]);
  assert.equal(result.unexpected[1].body, "net::ERR_CONNECTION_RESET");
});

test("a sign-out never answered opens no window", async () => {
  const result = await outcome(({ send, answer }) => {
    const status = send("GET", "/api/community/connection/status", "dana-1");
    send("POST", "/api/auth/logout", "dana-1");
    answer(status, 401, UNAUTHENTICATED);
  });
  assert.deepEqual(sessions(result), [["/api/community/connection/status", "live", false]]);
});

test("a 404 allowance matches its named code and detail, not_found by default", async () => {
  const missing = JSON.stringify({ detail: "No primary image found" });
  const coded = JSON.stringify({ code: "not_found", detail: "No primary image found" });
  const allowances = [
    { label: "dana", path: "/api/library-items/a/image", status: 404, reason: "missing artwork", detail: "No primary image found", code: null },
    { label: "dana", path: "/api/library-items/b/image", status: 404, reason: "foreign artwork", detail: "No primary image found" },
  ];
  const result = await outcome(({ send, answer }) => {
    answer(send("GET", "/api/library-items/a/image", "dana-1"), 404, missing);
    answer(send("GET", "/api/library-items/a/image", "dana-1"), 404, coded);
    answer(send("GET", "/api/library-items/b/image", "dana-1"), 404, missing);
    answer(send("GET", "/api/library-items/b/image", "dana-1"), 404, coded);
  }, allowances);
  assert.deepEqual(result.expected.map(entry => [entry.path, entry.code]), [["/api/library-items/a/image", null], ["/api/library-items/b/image", "not_found"]]);
  assert.deepEqual(result.unexpected.map(entry => [entry.path, entry.code]), [["/api/library-items/a/image", "not_found"], ["/api/library-items/b/image", null]]);
});
