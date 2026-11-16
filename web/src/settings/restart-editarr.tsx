import { useEffect, useRef, useState } from "react";

import { fetchRestartCapability, requestEditarrRestart } from "../api/api-service";
import { ApiError, AuthenticationStateError } from "../api/api-transport";
import { runtimeApiBasePath } from "../shared/runtime-urls";

type RestartPhase =
  | "ready"
  | "requesting"
  | "uncertain"
  | "restarting"
  | "reconnecting"
  | "cross_origin"
  | "recovered"
  | "unsupported"
  | "failed";

interface RestartEditarrProps {
  hasUnsavedChanges: boolean;
  pollIntervalMs?: number;
  maxPollAttempts?: number;
  requestTimeoutMs?: number;
  navigate?: (url: string) => void;
}

function reconnectApiBase(reconnectUrl: URL): string {
  const basePath = reconnectUrl.pathname.replace(/\/$/, "");
  return `${reconnectUrl.origin}${basePath}/api`;
}

interface DocumentLocation {
  readonly href: string;
  assign(url: string): void;
  reload(): void;
}

/** Open the restart target as a fresh document.
 * @param url Server-resolved reconnect URL.
 * @param location Browser location to navigate.
 */
export function openRestartTarget(url: string, location: DocumentLocation = window.location): void {
  const current = new URL(location.href);
  const target = new URL(url, current);
  location.assign(target.href);
  current.hash = "";
  target.hash = "";
  // A target that differs at most by fragment is a same-document navigation.
  if (target.href === current.href) location.reload();
}

/** Limit a restart request, including body reads, to the parent signal and timeout.
 * @param operation Request to run with the bounded signal.
 * @param parent Signal for the current restart attempt.
 * @param timeoutMs Maximum time to wait for the request.
 */
async function boundedRequest<T>(operation: (signal: AbortSignal) => Promise<T>, parent: AbortSignal, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort(parent.reason);
  parent.addEventListener("abort", cancel, { once: true });
  if (parent.aborted) cancel();
  const timer = window.setTimeout(() => controller.abort(new DOMException("Restart request timed out", "TimeoutError")), timeoutMs);
  let onAbort = () => {};
  try {
    const aborted = new Promise<never>((_, reject) => {
      onAbort = () => reject(controller.signal.reason);
      controller.signal.addEventListener("abort", onAbort, { once: true });
      if (controller.signal.aborted) onAbort();
    });
    return await Promise.race([aborted, operation(controller.signal)]);
  } finally {
    window.clearTimeout(timer);
    controller.signal.removeEventListener("abort", onAbort);
    parent.removeEventListener("abort", cancel);
  }
}

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cancel = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", cancel);
      reject(signal.reason);
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", cancel, { once: true });
    if (signal.aborted) cancel();
  });
}

/** Manage restart confirmation, request, and reconnection polling.
 * @param props Restart settings and navigation callback.
 */
export function RestartEditarr({
  hasUnsavedChanges,
  pollIntervalMs = 1_000,
  maxPollAttempts = 30,
  requestTimeoutMs = 5_000,
  navigate = openRestartTarget,
}: RestartEditarrProps) {
  const [phase, setPhase] = useState<RestartPhase>("ready");
  const [detail, setDetail] = useState<string | null>(null);
  const [reconnectUrl, setReconnectUrl] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const mounted = useRef(true);
  const activeRun = useRef<AbortController | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      activeRun.current?.abort();
    };
  }, []);

  const terminalFailure = (generation: number, outcome: string, message: string | null, targetGeneration: number): boolean => {
    if (generation < targetGeneration) return false;
    if (outcome === "recovered") {
      setPhase("recovered");
      setDetail(message || "The saved settings could not be applied. Editarr recovered on its previous address and the changes are still pending.");
      requestInFlight.current = false;
      return true;
    }
    if (outcome === "failed") {
      setPhase("failed");
      setDetail(message || "Editarr could not restart. Use the server logs and offline recovery instructions to restore the service.");
      requestInFlight.current = false;
      return true;
    }
    return false;
  };

  const pollForRestart = async (targetGeneration: number, target: URL, targetIsSameOrigin: boolean, signal: AbortSignal) => {
    const targetApiBase = reconnectApiBase(target);
    const currentApiBase = `${window.location.origin}${runtimeApiBasePath()}`;
    const pollsTarget = targetIsSameOrigin;
    const hasSeparateRecoveryChannel = targetApiBase !== currentApiBase;
    for (let attempt = 0; attempt < maxPollAttempts; attempt += 1) {
      await pause(pollIntervalMs, signal);
      if (!mounted.current) return;
      let targetUnavailable = !pollsTarget;
      if (pollsTarget) {
        try {
          const capability = await boundedRequest((requestSignal) => fetchRestartCapability(targetApiBase, { deferAuthenticationTransition: true, signal: requestSignal }), signal, requestTimeoutMs);
          if (!mounted.current) return;
          targetUnavailable = false;
          if (terminalFailure(capability.generation, capability.outcome, capability.detail, targetGeneration)) return;
          if (capability.generation >= targetGeneration && capability.outcome === "applied") {
            // This is the only successful terminal state we can observe at the
            // target. Release the guard before navigating so Restart stays
            // available if the browser does not leave this page.
            requestInFlight.current = false;
            setPhase("ready");
            setDetail("Restart complete. Reconnecting to General Settings…");
            navigate(target.href);
            return;
          }
          if (capability.generation < targetGeneration) {
            setPhase("restarting");
            setDetail("The previous service generation is still responding. Waiting for the replacement service.");
          } else {
            setPhase("reconnecting");
            setDetail("The replacement service is not ready yet. Waiting to reconnect.");
          }
        } catch (reason) {
          if (!mounted.current) return;
          if (reason instanceof AuthenticationStateError) {
            setPhase("reconnecting");
            setDetail("The restart changed the sign-in scope. Opening General Settings so you can sign in again…");
            navigate(target.href);
            return;
          }
          targetUnavailable = true;
          setPhase("reconnecting");
          setDetail("The service is temporarily unavailable. Waiting for it to come back online.");
        }
      }

      if (hasSeparateRecoveryChannel) {
        try {
          const current = await boundedRequest((requestSignal) => fetchRestartCapability(currentApiBase, { deferAuthenticationTransition: true, signal: requestSignal }), signal, requestTimeoutMs);
          if (!mounted.current) return;
          if (terminalFailure(current.generation, current.outcome, current.detail, targetGeneration)) return;
          // The old endpoint is only a failure/recovery channel. Even a new generation
          // reporting applied there cannot prove that a different saved target is live.
          if (current.generation < targetGeneration && pollsTarget && targetUnavailable) {
            setPhase("restarting");
            setDetail("The previous service generation is still responding. Waiting for the replacement service.");
          }
        } catch {
          // Expected after a successful origin or prefix transition. The target link
          // remains the only browser-safe cross-origin continuation.
        }
      }
    }
    if (!mounted.current) return;
    if (!pollsTarget) {
      setDetail("Restart was accepted, but its result at the different public address cannot be verified here. Status checks have stopped. Open the saved target when its proxy route is ready; you may need to sign in again.");
      return;
    }
    setPhase("failed");
    setDetail("Editarr did not become ready in time. Check the service or proxy, then open the saved restart target.");
    requestInFlight.current = false;
  };

  const restart = async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    const run = new AbortController();
    activeRun.current = run;
    let acknowledged = false;
    setPhase("requesting");
    setDetail("Requesting a service restart…");
    setReconnectUrl(null);
    try {
      const accepted = await boundedRequest(requestEditarrRestart, run.signal, requestTimeoutMs);
      if (!mounted.current) return;
      const target = new URL(accepted.reconnect_url);
      acknowledged = true;
      setReconnectUrl(target.href);
      const targetIsSameOrigin = target.origin === window.location.origin;
      if (!targetIsSameOrigin) {
        setPhase("cross_origin");
        setDetail("Restart accepted at a different public address. Open the saved target when its proxy route is ready; you may need to sign in again.");
        await pollForRestart(accepted.target_generation, target, false, run.signal);
        return;
      }
      setPhase("restarting");
      setDetail("Editarr is restarting. Saved deployment settings are being applied.");
      await pollForRestart(accepted.target_generation, target, true, run.signal);
    } catch (reason) {
      if (!mounted.current || run.signal.aborted) return;
      if (!acknowledged && !(reason instanceof ApiError)) {
        setPhase("uncertain");
        setDetail("The restart acknowledgment was not received. Editarr may already be restarting; no repeat request was sent. Reload General Settings to check the service. If its address changed, use the saved public address or check the service and proxy configuration.");
        return;
      }
      requestInFlight.current = false;
      if (reason instanceof ApiError && reason.code === "restart_unsupported") {
        setPhase("unsupported");
        setDetail(`${reason.message}. Start Editarr with the standalone editarr launcher to enable in-app restart.`);
      } else {
        setPhase("failed");
        setDetail(reason instanceof Error ? reason.message : "Unable to restart Editarr.");
      }
    } finally {
      if (activeRun.current === run) activeRun.current = null;
    }
  };

  const pending = phase === "requesting" || phase === "uncertain" || phase === "restarting" || phase === "reconnecting" || phase === "cross_origin";
  const statusRole = phase === "failed" || phase === "uncertain" || phase === "unsupported" || phase === "recovered" ? "alert" : "status";

  return <div className="restart-editarr-action">
    <button className="btn-secondary" type="button" onClick={() => void restart()} disabled={pending}>
      {phase === "requesting" ? "Requesting restart…" : "Restart Editarr"}
    </button>
    <p className="restart-editarr-saved-note">
      {hasUnsavedChanges
        ? "You have unsaved changes. Restart uses only settings already saved; these edits will not be applied."
        : "Restart applies only saved settings and still restarts Editarr when no changes are pending."}
    </p>
    {detail && <div className={`restart-editarr-status restart-editarr-${phase}`} role={statusRole}>
      <span>{detail}</span>
      {phase === "uncertain" && <button className="btn-secondary compact-button" type="button" onClick={() => window.location.reload()}>Reload General Settings</button>}
      {reconnectUrl && (phase === "cross_origin" || phase === "failed") && <a className="btn-secondary compact-button" href={reconnectUrl}>Open restart target</a>}
    </div>}
  </div>;
}
