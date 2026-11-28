import { useCallback, useEffect, useState } from "react";
import logo from "../../../public/apple-touch-icon.png";
import { OperatorAccessError, readOperatorSession, type OperatorSession } from "./runtime";
import { OperatorApp } from "./shell/operator-app";

type AccessState = { kind: "checking" } | { kind: "verified"; session: OperatorSession } |
  { kind: "denied" | "unavailable" | "sign-in" };

/** Verify the Access session before any operator data loads; failures never fall back to local sign-in. */
export function OperatorAccess() {
  const [state, setState] = useState<AccessState>({ kind: "checking" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: "checking" });
    void readOperatorSession(controller.signal).then((session) => {
      if (!controller.signal.aborted) setState({ kind: "verified", session });
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setState({ kind: error instanceof OperatorAccessError && error.status === 401 ? "sign-in" :
        error instanceof OperatorAccessError && error.status === 403 ? "denied" : "unavailable" });
    });
    return () => controller.abort();
  }, [attempt]);
  const onAccessFailure = useCallback((status: number) => setState({ kind: status === 401 ? "sign-in" : "denied" }), []);
  const reverify = useCallback(() => setAttempt(value => value + 1), []);

  if (state.kind === "verified") return <>
    <p role="status" className="sr">Access verified</p>
    <OperatorApp session={state.session} onAccessFailure={onAccessFailure} onReverify={reverify} />
  </>;
  const label = { checking: "Checking access...", denied: "Operator access denied",
    unavailable: "Access verification unavailable", "sign-in": "Sign-in required" }[state.kind];
  return <div className="access-page">
    <main className="access-card">
      <header className="brand"><img src={logo} alt="" width="30" height="30" /><div>Editarr Community<small>OPERATOR</small></div></header>
      <h1>Operator access</h1>
      <section aria-label="Access status" className={`access-status access-${state.kind}`}>
        <p role={state.kind === "denied" || state.kind === "unavailable" ? "alert" : "status"}>{label}</p>
      </section>
      {state.kind === "sign-in" ? <button className="btn primary" onClick={() => window.location.reload()}>Sign in</button> :
        <button className="btn" disabled={state.kind === "checking"} onClick={reverify}>Refresh access</button>}
    </main>
  </div>;
}
