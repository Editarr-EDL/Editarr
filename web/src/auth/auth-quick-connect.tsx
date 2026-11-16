import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelQuickConnect,
  completeQuickConnect,
  fetchQuickConnectStatus,
  initiateQuickConnect,
} from "../api/api-auth";
import type { AuthenticationStatus } from "../api/api-system-types";
import { ApiError } from "../api/api-transport";
import { RetryAfterNotice, useRetryAfter } from "./retry-after";
import { signInError } from "./sign-in-errors";

export const QUICK_CONNECT_POLL_MS = 1000;
const OUTAGE_BACKOFF_CAP_MS = 8000;
const OUTAGE_GIVE_UP_MS = 30_000;

type View =
  | { phase: "starting" }
  | { phase: "waiting"; code: string; notice?: string }
  | { phase: "signing_in"; code: string }
  | { phase: "expired" }
  | { phase: "stopped"; message: string };

const PROGRESS: Partial<Record<View["phase"], string>> = {
  starting: "Getting a code from Jellyfin…",
  waiting: "Waiting for approval…",
  signing_in: "Approved. Signing in…",
};
const CODE_ENDED = "This code can no longer sign in. Get a new code.";
const ENDED_CODES = new Set(["quick_connect_pending", "quick_connect_cancelled", "quick_connect_consumed"]);
// Editarr forgets every code when it restarts, so a code it no longer knows has expired.
const EXPIRED_CODES = new Set(["quick_connect_not_found", "quick_connect_expired"]);

const UNREACHABLE = {
  jellyfin: {
    notice: "Jellyfin could not be reached. Still trying…",
    stopped: "Jellyfin could not be reached, so Quick Connect stopped. Try again when Jellyfin is back.",
  },
  editarr: {
    notice: "Editarr could not be reached. Still trying…",
    stopped: "Editarr could not be reached, so Quick Connect stopped. Try again when Editarr is back.",
  },
};

/** Which server a failure says is down: a network error or a 5xx may pass, so polling waits it out. */
function unreachable(reason: unknown) {
  if (!(reason instanceof ApiError)) return UNREACHABLE.editarr;
  if (reason.status < 500) return null;
  return reason.code === "jellyfin_unavailable" ? UNREACHABLE.jellyfin : UNREACHABLE.editarr;
}

function stoppedMessage(reason: unknown): string {
  const code = reason instanceof ApiError ? reason.code : undefined;
  if (code === "jellyfin_unavailable") return UNREACHABLE.jellyfin.stopped;
  if (code && ENDED_CODES.has(code)) return CODE_ENDED;
  if (!code && unreachable(reason)) return UNREACHABLE.editarr.stopped;
  return signInError(reason);
}

function checkingAgain(seconds: number): string {
  return `Checking again in ${seconds} second${seconds === 1 ? "" : "s"}.`;
}

function stoppedView(reason: unknown): View {
  const code = reason instanceof ApiError ? reason.code : undefined;
  return code && EXPIRED_CODES.has(code) ? { phase: "expired" } : { phase: "stopped", message: stoppedMessage(reason) };
}

/**
 * Show a Jellyfin Quick Connect code and sign in once a Jellyfin user approves it.
 * Polls until Jellyfin approves or expires the code, an outage outlasts about thirty
 * seconds, or the person cancels; cancelling ends the attempt on the server too.
 */
export function QuickConnectSignIn({ rememberMe, onSignedIn, onCancel }: {
  rememberMe: boolean;
  onSignedIn: (status: AuthenticationStatus) => void;
  onCancel: () => void;
}) {
  const [view, setView] = useState<View>({ phase: "starting" });
  const throttle = useRetryAfter();
  const latest = useRef({ rememberMe, onSignedIn, waitFor: throttle.waitFor });
  latest.current = { rememberMe, onSignedIn, waitFor: throttle.waitFor };
  const mounted = useRef(false);
  const started = useRef(false);

  // Starting is not an effect that may run twice: each start replaces the browser's attempt.
  const start = useCallback(() => {
    setView({ phase: "starting" });
    initiateQuickConnect().then((attempt) => {
      if (!mounted.current) return void cancelQuickConnect().catch(() => undefined);
      setView({ phase: "waiting", code: attempt.code });
    }, (reason) => {
      if (!mounted.current) return;
      latest.current.waitFor(reason);
      setView({ phase: "stopped", message: stoppedMessage(reason) });
    });
  }, []);

  useEffect(() => {
    mounted.current = true;
    if (!started.current) {
      started.current = true;
      start();
    }
    return () => { mounted.current = false; };
  }, [start]);

  const waitingFor = view.phase === "waiting" ? view.code : null;
  useEffect(() => {
    if (waitingFor === null) return;
    let active = true;
    let outageSince: number | null = null;
    let outageFailures = 0;
    const stop = (reason: unknown) => { if (mounted.current) setView(stoppedView(reason)); };
    const pollIn = (delay: number) => { timer = window.setTimeout(() => void poll(), delay); };
    const waitOut = (reason: unknown) => {
      if (reason instanceof ApiError && reason.status === 429) {
        const seconds = Math.max(reason.retryAfterSeconds ?? 0, 1);
        setView({ phase: "waiting", code: waitingFor, notice: checkingAgain(seconds) });
        return pollIn(seconds * 1000);
      }
      const outage = unreachable(reason);
      if (!outage) return stop(reason);
      outageSince ??= Date.now();
      if (Date.now() - outageSince >= OUTAGE_GIVE_UP_MS) return setView({ phase: "stopped", message: outage.stopped });
      setView({ phase: "waiting", code: waitingFor, notice: outage.notice });
      pollIn(Math.min(QUICK_CONNECT_POLL_MS * 2 ** outageFailures++, OUTAGE_BACKOFF_CAP_MS));
    };
    const poll = async () => {
      let attempt;
      try {
        attempt = await fetchQuickConnectStatus();
      } catch (reason) {
        if (active) waitOut(reason);
        return;
      }
      if (!active) return;
      outageSince = null;
      outageFailures = 0;
      if (attempt.state === "pending") {
        setView((current) => current.phase === "waiting" && current.notice ? { phase: "waiting", code: current.code } : current);
        pollIn(QUICK_CONNECT_POLL_MS);
        return;
      }
      if (attempt.state !== "approved") {
        setView(attempt.state === "expired" ? { phase: "expired" } : { phase: "stopped", message: CODE_ENDED });
        return;
      }
      setView({ phase: "signing_in", code: attempt.code });
      try {
        const status = await completeQuickConnect({ remember_me: latest.current.rememberMe });
        if (mounted.current) latest.current.onSignedIn(status);
      } catch (reason) {
        stop(reason);
      }
    };
    let timer = window.setTimeout(() => void poll(), QUICK_CONNECT_POLL_MS);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [waitingFor]);

  const cancel = () => {
    void cancelQuickConnect().catch(() => undefined);
    onCancel();
  };
  const code = view.phase === "waiting" || view.phase === "signing_in" ? view.code : null;
  const codeOutput = useRef<HTMLOutputElement>(null);
  useEffect(() => { if (code) codeOutput.current?.focus(); }, [code]);
  const progress = (view.phase === "waiting" && view.notice) || PROGRESS[view.phase] || "";

  return <section className="auth-form auth-quick-connect" aria-labelledby="quick-connect-heading" aria-busy={view.phase === "starting" || view.phase === "signing_in"}>
    <h2 id="quick-connect-heading" className="auth-subheading">Quick Connect</h2>
    {code && <>
      <p className="auth-field-hint">In a Jellyfin app where you are signed in, open Quick Connect and enter this code.</p>
      <output ref={codeOutput} tabIndex={-1} className="auth-quick-connect-code" aria-label="Quick Connect code">{code}</output>
      <p className="auth-field-hint">Only approve a code you started here.</p>
    </>}
    <p className="auth-field-hint" role="status">{progress}</p>
    {view.phase === "expired" && <p className="auth-form-error" role="alert">This code expired. Get a new code to try again.</p>}
    {view.phase === "stopped" && <p className="auth-form-error" role="alert">{view.message}<RetryAfterNotice retry={throttle} /></p>}
    {(view.phase === "expired" || view.phase === "stopped") && <button className="auth-submit btn-primary" type="button" onClick={start} disabled={throttle.remaining > 0}>
      Get a new code
    </button>}
    <button className="btn-secondary" type="button" onClick={cancel} disabled={view.phase === "signing_in"}>
      Cancel
    </button>
  </section>;
}
