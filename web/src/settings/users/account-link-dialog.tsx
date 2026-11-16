import { useId, useReducer, useState, type FormEvent } from "react";
import { confirmLink, proveLink, type LinkKind, type LinkResult, type ProvenLink } from "../../api/api-account-link";
import { FreshAuthenticationDialog } from "../../auth/fresh-authentication";
import { ModalDialog } from "../../ui/ModalDialog";
import { isPasswordRefusal, linkFlow, linkResultMessage } from "./account-link-flow";
import { LinkPreviewView } from "./account-link-preview";
import { DuplicateIdentityResolution } from "./account-deletion-duplicate";

export interface LinkTarget { id: number; name: string; own: boolean; kind: LinkKind }

/**
 * Prove, review, and confirm one account link. Passwords stay in the proof form only
 * until its request ends; the proof token is dropped when the dialog closes.
 */
export function AccountLinkDialog({ target, onClose, onLinked, onAccountDeleted }: { target: LinkTarget; onClose: () => void; onLinked: (result: LinkResult) => void; onAccountDeleted?: () => void }) {
  const [flow, dispatch] = useReducer(linkFlow, { step: "prove", notice: null });
  const [confirming, setConfirming] = useState(false);
  const title = target.kind === "link" ? "Link to Jellyfin" : "Relink to the current Jellyfin server";
  return <>
    <ModalDialog labelledBy="account-link-heading" className="account-dialog link-dialog" onClose={confirming ? undefined : onClose}>
      <div className="section-heading"><h2 id="account-link-heading">{title}</h2><button type="button" className="btn-secondary" onClick={onClose}>Close</button></div>
      <p className="link-target">{target.own ? "Your account" : target.name}</p>
      {flow.step === "prove" && <ProveForm target={target} notice={flow.notice} onProven={proven => dispatch({ type: "proven", proven })} onRefused={reason => dispatch({ type: "refused", reason })} />}
      {flow.step === "review" && <section className="link-review" aria-label="Link impact">
        {flow.notice && <p role="alert" className="form-status">{flow.notice}</p>}
        <LinkPreviewView preview={flow.proven.preview} />
        {flow.proven.preview.blockers.includes("jellyfin_identity_in_use") && <DuplicateIdentityResolution jellyfinUserId={flow.proven.preview.identity.after.user_id} linkTarget={{ id: target.id, name: target.own ? "your account" : target.name, active: flow.proven.preview.status.current === "active" }}
          onResolved={notice => { onAccountDeleted?.(); dispatch({ type: "restart", notice }); }} />}
        <p className="link-expiry">This proof works once and expires at {new Date(flow.proven.expires_at).toLocaleTimeString()}.</p>
        <div className="button-row">
          <button type="button" className="btn-secondary" onClick={() => dispatch({ type: "restart" })}>Start again</button>
          {flow.proven.preview.blockers.length === 0 && <button type="button" className="btn-primary" onClick={() => setConfirming(true)}>{target.kind === "link" ? "Confirm link" : "Confirm relink"}</button>}
        </div>
      </section>}
      {flow.step === "finished" && <Finished result={flow.result} onClose={onClose} />}
      {flow.step === "stopped" && <section className="link-stopped" aria-label="Link stopped">
        <p role="alert" className="form-status">{flow.message}</p>
        <div className="button-row"><button type="button" className="btn-secondary" onClick={onClose}>Close</button><button type="button" className="btn-primary" onClick={() => dispatch({ type: "restart" })}>Start again</button></div>
      </section>}
    </ModalDialog>
    {confirming && flow.step === "review" && <FreshAuthenticationDialog title={target.kind === "link" ? "Confirm link" : "Confirm relink"}
      body="New export files are written before anything changes. Old files are removed only after the switch succeeds."
      actionLabel={target.kind === "link" ? "Link account" : "Relink account"}
      onClose={() => setConfirming(false)}
      onConfirm={password => confirm(flow.proven, password)} />}
  </>;

  async function confirm(proven: ProvenLink, password: string) {
    try {
      const result = await confirmLink(target.id, password, proven);
      dispatch({ type: "linked", result });
      onLinked(result);
    } catch (reason) {
      if (isPasswordRefusal(reason)) throw reason;
      dispatch({ type: "refused", reason });
    }
    setConfirming(false);
  }
}

function ProveForm({ target, notice, onProven, onRefused }: {
  target: LinkTarget; notice: string | null; onProven: (proven: ProvenLink) => void; onRefused: (reason: unknown) => void;
}) {
  const id = useId();
  const [username, setUsername] = useState("");
  const [jellyfinPassword, setJellyfinPassword] = useState("");
  const [authenticating, setAuthenticating] = useState(false);
  function forget() { setJellyfinPassword(""); setAuthenticating(false); }
  async function prove(password: string) {
    try {
      onProven(await proveLink(target.id, { password, jellyfin: { username: username.trim(), password: jellyfinPassword } }));
    } catch (reason) { onRefused(reason); }
    finally { forget(); }
  }
  return <><form className="link-prove" onSubmit={(event: FormEvent) => { event.preventDefault(); setAuthenticating(true); }}>
    <p>{target.kind === "link"
      ? "Sign in as the Jellyfin user this account belongs to. Matching names prove nothing."
      : "The configured Jellyfin server replaced the one this account was linked to. Sign in as this person's Jellyfin user on the current server."}</p>
    {notice && <p role="alert" className="form-status">{notice}</p>}
    <div className="account-fields">
      <label className="field" htmlFor={`${id}-user`}><span>Jellyfin username</span><input id={`${id}-user`} required autoComplete="off" maxLength={256} value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label className="field" htmlFor={`${id}-password`}><span>Jellyfin password</span><input id={`${id}-password`} type="password" autoComplete="off" maxLength={1024} value={jellyfinPassword} onChange={event => setJellyfinPassword(event.target.value)} /></label>
    </div>
    {!target.own && <p className="link-hint">Your confirmation that this Jellyfin user is the same person is recorded as an admin attestation.</p>}
    <div className="button-row"><button type="submit" className="btn-primary">Review impact</button></div>
  </form>
    {authenticating && <FreshAuthenticationDialog title="Confirm your Jellyfin password" body="Your password and the Jellyfin sign-in are sent once to prove this link. Neither is stored." actionLabel="Prove identities"
      onClose={forget} onConfirm={prove} />}
  </>;
}

function Finished({ result, onClose }: { result: LinkResult; onClose: () => void }) {
  return <section className="link-finished" aria-label="Link result">
    <p role="status">{linkResultMessage(result)}</p>
    <div className="button-row"><button type="button" className="btn-primary" onClick={onClose}>Done</button></div>
  </section>;
}
