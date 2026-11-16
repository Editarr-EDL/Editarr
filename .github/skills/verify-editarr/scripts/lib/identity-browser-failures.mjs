const SIGN_OUTS = new Set(["POST /api/auth/logout"]);

const route = request => `${request.method()} ${new URL(request.url()).pathname}`;

const settle = (promise, fallback) => Promise.race([
  Promise.resolve(promise).catch(error => fallback(error)),
  new Promise(resolve => setTimeout(() => resolve(fallback(new Error("timed out"))), 5000).unref()),
]);

const sessionOf = async request => /(?:^|;\s*)editarr_session=([^;]+)/.exec((await settle(request.allHeaders(), () => ({}))).cookie ?? "")?.[1] ?? null;

/**
 * Record every browser `/api/` response of 400 or more on the pages it watches. Each
 * page keeps an event clock and a log of the sessions its requests carried, so a
 * failure can be placed against the journey's own sign-outs (logout, or a password
 * change, which also ends the session). Only an answered, successful sign-out ends a
 * session. One that fails at the network layer is itself recorded as a failure.
 */
export function browserFailures() {
  const failures = [], pending = [], signOuts = [];

  function watch(actor) {
    let clock = 0;
    const sent = new Map(), log = [], own = [];
    actor.page.on("request", request => {
      const entry = { request, at: ++clock, token: sessionOf(request), signOut: null };
      if (SIGN_OUTS.has(route(request))) {
        entry.signOut = { label: actor.label, request, token: entry.token, answered: null, ended: false, error: null };
        signOuts.push(entry.signOut);
      }
      sent.set(request, entry); log.push(entry);
    });
    actor.page.on("requestfailed", request => {
      const signOut = sent.get(request)?.signOut;
      if (signOut) { signOut.answered = ++clock; signOut.error = request.failure()?.errorText ?? "request failed"; }
    });
    actor.page.on("response", response => {
      const at = ++clock;
      const request = response.request();
      const signOut = sent.get(request)?.signOut;
      if (signOut) { signOut.answered = at; signOut.ended = response.status() < 400; }
      if (response.status() < 400 || !new URL(response.url()).pathname.startsWith("/api/")) return;
      const read = describe(actor.label, response, sent.get(request), at, log);
      own.push(read); pending.push(read);
    });
    return () => Promise.all(own);
  }

  async function describe(label, response, entry, at, log) {
    const request = response.request();
    let bodyRead = true;
    const text = await settle(response.text(), error => { bodyRead = false; return `<body unavailable: ${String(error.message).split("\n")[0]}>`; });
    let code = null;
    try { code = JSON.parse(text).code ?? null; } catch { code = null; }
    const tokens = await Promise.all(log.map(item => item.token));
    failures.push({ label, method: request.method(), path: new URL(response.url()).pathname, status: response.status(), code, body: text.slice(0, 300), bodyRead,
      sentAt: entry?.at ?? 0, at, token: await entry?.token ?? null, events: log.map((item, index) => ({ at: item.at, token: tokens[index], signOut: item.signOut, self: item === entry })) });
  }

  return { watch, settled: () => Promise.all(pending), outcome: (allowances, pageErrors) => browserOutcome(failures, signOuts, allowances, pageErrors) };
}

/**
 * `session` is "none" when the request carried no cookie and the page held no live
 * session when it left, "dropped" when it carried no cookie although the page did,
 * "signed-out" when the page had issued a sign-out for that cookie before the failure
 * arrived, that sign-out was answered and ended the session, and the request left
 * before that answer, and "live" otherwise. For a cookieless request, a session is
 * live from the first request that carries it until a sign-out of it is issued that
 * is later answered successfully. Playwright can report the next request before the
 * sign-out's response, although the browser already dropped the cookie by then.
 */
function sessionAt(failure) {
  const endedBy = (token, before) => failure.events.some(event => event.signOut?.ended && event.token === token && event.at < before);
  if (failure.token === null) {
    const live = failure.events.some(event => event.at < failure.sentAt && event.token !== null && !endedBy(event.token, failure.sentAt));
    return live ? "dropped" : "none";
  }
  const inFlight = failure.events.some(event => !event.self && event.signOut?.ended && event.token === failure.token && event.at < failure.at && failure.sentAt < event.signOut.answered);
  return inFlight ? "signed-out" : "live";
}

/**
 * Split browser API failures into expected refusals and unexpected ones. A 401 is
 * expected only for a request with no session on a page that held none, or one the
 * journey itself was signing out. The latter may have no body: the app aborts its
 * polls when it signs out, and the browser then discards the 401's body. Anything else needs an `allow` entry for the same
 * actor, path, and status; a 404 must carry the entry's `code` (`not_found` unless it
 * names another) and, when given, its detail. A sign-out that fails at the network
 * layer is unexpected; one never answered opens no window.
 */
function browserOutcome(failures, signOuts, allowances, pageErrors) {
  const reason = failure => {
    if (failure.status === 401 && failure.code === "authentication_required" && failure.session === "none") return "signed-out shell probe";
    if (failure.status === 401 && failure.code === "authentication_required" && failure.session === "signed-out") return "in flight while the journey signed this session out";
    if (failure.status === 401 && !failure.bodyRead && failure.session === "signed-out") return "in flight while the journey signed this session out; the page aborted it, so the browser kept no body";
    const match = allowances.find(entry => entry.label === failure.label && entry.status === failure.status && (failure.path === entry.path || failure.path.startsWith(`${entry.path}/`))
      && (failure.status !== 404 || (failure.code === (entry.code === undefined ? "not_found" : entry.code) && (!entry.detail || failure.body.includes(entry.detail)))));
    return match?.reason ?? null;
  };
  const answered = failures.map(({ label, method, path, status, code, body, bodyRead, ...failure }) => ({ label, method, path, status, code, body, bodyRead, session: sessionAt(failure) }));
  const unanswered = signOuts.filter(entry => entry.error !== null)
    .map(entry => ({ label: entry.label, method: entry.request.method(), path: new URL(entry.request.url()).pathname, status: null, code: null, body: entry.error, bodyRead: false, session: "n/a" }));
  const labelled = [...answered, ...unanswered].map(failure => ({ ...failure, expected: failure.status === null ? null : reason(failure) }));
  return { expected: labelled.filter(entry => entry.expected), unexpected: labelled.filter(entry => !entry.expected), allowances, pageErrors: [...pageErrors] };
}
