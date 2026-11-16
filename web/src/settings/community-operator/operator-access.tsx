import { useEffect, useState } from "react";
import logo from "../../../public/favicon-32.png";
import { OperatorAccessError, readOperatorSession, type OperatorSession } from "./runtime";
import { OperatorDashboard } from "./dashboard";

type AccessState = { kind: "checking" } | { kind: "verified"; session: OperatorSession } |
  { kind: "denied" | "unavailable" | "sign-in" };

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

  const label = { checking: "Checking access...", verified: "Access verified", denied: "Operator access denied",
    unavailable: "Access verification unavailable", "sign-in": "Sign-in required" }[state.kind];
  return <div className="operator-shell">
    <header className="operator-brand"><img src={logo} alt="" /><span>Editarr Community</span></header>
    <main className="operator-main">
      <div className="operator-heading"><h1>Operator access</h1>
        {state.kind === "sign-in" ? <button onClick={() => window.location.reload()}>Sign in</button> :
          <button disabled={state.kind === "checking"} onClick={() => setAttempt((value) => value + 1)}>Refresh access</button>}
      </div>
      <section aria-label="Access status" className={`operator-status operator-${state.kind}`}>
        <p role={state.kind === "denied" || state.kind === "unavailable" ? "alert" : "status"}>{label}</p>
        {state.kind === "verified" && <dl><dt>Operator subject</dt><dd>{state.session.subject}</dd></dl>}
      </section>
      {state.kind === "verified" && <OperatorDashboard onAccessFailure={(status) => setState({ kind: status === 401 ? "sign-in" : "denied" })} />}
    </main>
  </div>;
}