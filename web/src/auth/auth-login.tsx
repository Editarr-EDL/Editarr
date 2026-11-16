import { useEffect, useState, type FormEvent } from "react";

import { fetchQuickConnectAvailability, loginWithJellyfin } from "../api/api-auth";
import { applicationAssetUrl } from "../shared/runtime-urls";
import { useAuthenticationGate } from "./auth-gate";
import { QuickConnectSignIn } from "./auth-quick-connect";
import { RetryAfterNotice, useRetryAfter } from "./retry-after";
import { signInError } from "./sign-in-errors";

/** Sign in with a Jellyfin password or, when Jellyfin allows it, Quick Connect. */
export function AdministratorLogin() {
  const { acceptAuthenticationStatus, session, signInNotice } = useAuthenticationGate();
  const jellyfin = session?.kind === "signed_out" && session.signIn.jellyfin;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(signInNotice);
  const [pending, setPending] = useState(false);
  const retry = useRetryAfter();
  const [quickConnectOffered, setQuickConnectOffered] = useState(false);
  const [quickConnect, setQuickConnect] = useState(false);

  useEffect(() => {
    if (!jellyfin) return;
    let active = true;
    fetchQuickConnectAvailability().then((enabled) => { if (active) setQuickConnectOffered(enabled); }, () => undefined);
    return () => { active = false; };
  }, [jellyfin]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending || retry.remaining > 0) return;
    setFormError(null);
    setNotice(null);
    setPending(true);
    try {
      const status = await loginWithJellyfin({ username, password, remember_me: rememberMe });
      setPassword("");
      acceptAuthenticationStatus(status);
    } catch (reason) {
      retry.waitFor(reason);
      setPassword("");
      setFormError(signInError(reason));
      setPending(false);
    }
  };

  return <main className="auth-page">
    <section className="auth-panel" aria-labelledby="sign-in-heading" aria-busy={pending}>
      <header className="auth-brand">
        <img src={applicationAssetUrl("editarr-app-icon.png?v=1")} alt="" width="48" height="48" />
        <span>Editarr</span>
      </header>
      <div className="auth-heading">
        <p className="eyebrow">Welcome back</p>
        <h1 id="sign-in-heading">SIGN IN TO CONTINUE</h1>
        <p>{!jellyfin ? "Sign-in is unavailable because Editarr is not connected to a Jellyfin server." : quickConnect ? "Approve a code in Jellyfin to sign in." : "Sign in with your Jellyfin username and password."}</p>
      </div>
      {!jellyfin ? null : quickConnect ? <QuickConnectSignIn
        rememberMe={rememberMe}
        onSignedIn={acceptAuthenticationStatus}
        onCancel={() => setQuickConnect(false)}
      /> : <form className="auth-form" onSubmit={(event) => void submit(event)} noValidate>
        <div className="auth-field">
          <label htmlFor="administrator-login-username">Jellyfin username</label>
          <input
            id="administrator-login-username"
            name="username"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={pending}
          />
        </div>
        <div className="auth-field">
          <label htmlFor="administrator-login-password">Jellyfin password</label>
          <input
            id="administrator-login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={pending}
          />
        </div>
        <label className="auth-remember">
          <input
            name="remember_me"
            type="checkbox"
            checked={rememberMe}
            onChange={(event) => setRememberMe(event.target.checked)}
            disabled={pending}
          />
          <span>Remember me</span>
        </label>
        {notice && <p className="auth-success" role="status">{notice}</p>}
        {formError && <p className="auth-form-error" role="alert" aria-live="assertive">{formError}<RetryAfterNotice retry={retry} /></p>}
        <button className="auth-submit btn-primary" type="submit" disabled={pending || retry.remaining > 0}>
          {pending ? "Signing in…" : "Sign in"}
        </button>
        {quickConnectOffered && <button className="btn-secondary" type="button" onClick={() => { setQuickConnect(true); setPassword(""); setFormError(null); setNotice(null); }} disabled={pending}>
          Use Quick Connect
        </button>}
      </form>}
    </section>
  </main>;
}
