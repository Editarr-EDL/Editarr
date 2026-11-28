import { useEffect, useState } from "react";
import { writeOperatorBackupConfig, writeOperatorBackupTest, type BackupConfig, type BackupDestination,
  type BackupFrequency, type OperatorBackups } from "../../../api/api-community-operator-system";
import { bytes } from "../shared/format";
import { PanelHead } from "../shared/ui";
import { mutationMessage, useMutation } from "./backup-mutation";

const frequencies: [BackupFrequency, string][] = [["hourly", "Hourly"], ["every_6h", "Every 6 hours"], ["daily", "Daily"], ["weekly", "Weekly"]];
const configOf = (data: OperatorBackups): BackupConfig => ({ enabled: data.config.enabled, frequency: data.config.frequency, atUtc: data.config.atUtc,
  keepLast: data.config.keepLast, keepWeekly: data.config.keepWeekly, destination: data.config.destination, integrityCheck: data.config.integrityCheck });
const same = (a: BackupConfig, b: BackupConfig) => (Object.keys(a) as (keyof BackupConfig)[]).every(key => a[key] === b[key]);

/** Client-side validation matching the service limits; returns a message per invalid field. */
export function configErrors(config: BackupConfig) {
  const errors: Partial<Record<keyof BackupConfig, string>> = {};
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(config.atUtc)) errors.atUtc = "Use 24-hour HH:MM, for example 03:30.";
  if (!Number.isInteger(config.keepLast) || config.keepLast < 1 || config.keepLast > 365) errors.keepLast = "Keep between 1 and 365 backups.";
  if (!Number.isInteger(config.keepWeekly) || config.keepWeekly < 0 || config.keepWeekly > 104) errors.keepWeekly = "Keep between 0 and 104 weekly backups.";
  const destination = destinationError(config.destination);
  if (destination) errors.destination = destination;
  return errors;
}
const segment = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$/;
/**
 * The service's destination rules (normalizeDestination) for a folder relative to the backup root: at most 512 characters,
 * no control characters or backslashes, no leading "/" or "..", up to 4 segments of letters, digits, ".", "_" or "-",
 * and not inside the reserved "staged" folder. Returns a message, or null when valid ("" is the root itself).
 */
export function destinationError(value: string) {
  const rest = value.trim();
  if (rest.length > 512 || /[\u0000-\u001f\u007f\\]/.test(rest)) return "Use letters, digits, '.', '_', '-' and '/' only.";
  if (rest.startsWith("/") || rest.split("/").includes("..")) return "Use a folder inside the backup root, without .. or a leading /.";
  const parts = rest.split("/").filter(Boolean);
  if (parts.length > 4) return "Use at most 4 folder levels.";
  if (parts.some(part => !segment.test(part))) return "Each folder name uses letters, digits, '.', '_' or '-' and does not start with '.'.";
  if (parts[0] === "staged") return "The staged folder is reserved for restores.";
  return null;
}

/** The backup schedule form: frequency, time, retention, destination inside the configured root and integrity check. */
export function ScheduleForm({ data, onSaved }: { data: OperatorBackups; onSaved: (value: OperatorBackups) => void }) {
  const [draft, setDraft] = useState<BackupConfig>(() => configOf(data));
  const [tested, setTested] = useState<BackupDestination | null>(null);
  const mutation = useMutation();
  useEffect(() => { setDraft(configOf(data)); }, [data.config.updatedAt]);
  const errors = configErrors(draft), dirty = !same(draft, configOf(data)), root = data.destination.root;
  const set = <K extends keyof BackupConfig>(key: K, value: BackupConfig[K]) => setDraft(current => ({ ...current, [key]: value }));
  const destination = tested ?? data.destination;
  const save = () => mutation.run(signal => writeOperatorBackupConfig(draft, signal), saved => { onSaved(saved); return "Schedule saved and audited."; });
  const test = () => mutation.run(signal => writeOperatorBackupTest(draft.destination, signal), result => {
    setTested(result.destination);
    return result.destination.writable ? `Destination writable${result.destination.freeBytes !== null ? ` · ${bytes(result.destination.freeBytes)} free` : ""}.`
      : `Destination not writable (${mutationMessage(result.destination.error ?? "unknown")}).`;
  });
  const field = (key: keyof BackupConfig) => errors[key] ? { "aria-invalid": true, "aria-describedby": `backup-${key}-error` } : {};
  return <section className="card" aria-labelledby="schedule-title">
    <PanelHead title={<span id="schedule-title">Schedule</span>}><span className={`chip ${data.config.enabled ? "ok" : ""}`}>{data.config.enabled ? "enabled" : "off"}</span></PanelHead>
    <form className="form" noValidate onSubmit={event => { event.preventDefault(); if (!Object.keys(errors).length && dirty) save(); }}>
      {!root && <p className="banner warn" role="note">Backups are disabled until the operator sets <code>COMMUNITY_BACKUP_ROOT</code> on the service host.</p>}
      <label className="check"><input type="checkbox" checked={draft.enabled} disabled={!root} onChange={event => set("enabled", event.target.checked)} />
        Run scheduled backups</label>
      <div className="field-row">
        <div className="field"><label htmlFor="backup-frequency">Frequency</label>
          <select id="backup-frequency" value={draft.frequency} onChange={event => set("frequency", event.target.value as BackupFrequency)}>
            {frequencies.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
        <div className="field"><label htmlFor="backup-atUtc">Time (UTC)</label>
          <input id="backup-atUtc" value={draft.atUtc} inputMode="numeric" maxLength={5} {...field("atUtc")} onChange={event => set("atUtc", event.target.value)} />
          {errors.atUtc && <small id="backup-atUtc-error" className="error">{errors.atUtc}</small>}</div>
      </div>
      <div className="field-row">
        <div className="field"><label htmlFor="backup-keepLast">Keep last</label>
          <input id="backup-keepLast" type="number" min={1} max={365} value={draft.keepLast} {...field("keepLast")}
            onChange={event => set("keepLast", Number(event.target.value))} />
          {errors.keepLast && <small id="backup-keepLast-error" className="error">{errors.keepLast}</small>}</div>
        <div className="field"><label htmlFor="backup-keepWeekly">Also keep weekly</label>
          <input id="backup-keepWeekly" type="number" min={0} max={104} value={draft.keepWeekly} {...field("keepWeekly")}
            onChange={event => set("keepWeekly", Number(event.target.value))} />
          {errors.keepWeekly && <small id="backup-keepWeekly-error" className="error">{errors.keepWeekly}</small>}</div>
      </div>
      <small className="hint">Older backups are deleted only after a newer one passes verification; the newest verified backup is never deleted.</small>
      <div className="field"><label htmlFor="backup-destination">Destination folder</label>
        <div className="prefixed">{root && <span className="mono prefix">{root.replace(/\/$/, "")}/</span>}
          <input id="backup-destination" className="mono" value={draft.destination} maxLength={500} placeholder="(backup root)" {...field("destination")}
            onChange={event => { set("destination", event.target.value); setTested(null); }} /></div>
        {errors.destination ? <small id="backup-destination-error" className="error">{errors.destination}</small> :
          <small>On the service host. {destination.configured ? `${destination.writable ? "Writable" : "Not writable"}${destination.freeBytes !== null
            ? ` · ${bytes(destination.freeBytes)} free` : ""}.` : "Not configured."}</small>}</div>
      <label className="check"><input type="checkbox" checked={draft.integrityCheck} onChange={event => set("integrityCheck", event.target.checked)} />
        Run an integrity check on each backup (PRAGMA integrity_check on the copy)</label>
      {mutation.message && <p role={mutation.failed ? "alert" : "status"} className={mutation.failed ? "error" : "muted"}>{mutation.message}</p>}
      <div className="form-actions">
        <button type="button" className="btn ghost" disabled={!dirty || mutation.busy} onClick={() => { setDraft(configOf(data)); setTested(null); }}>Discard</button>
        <span className="actions"><button type="button" className="btn" disabled={!root || mutation.busy || !!errors.destination} onClick={test}>Test destination</button>
          <button type="submit" className="btn primary" disabled={!root || !dirty || mutation.busy || Object.keys(errors).length > 0}>
            {mutation.busy ? "Saving…" : "Save schedule"}</button></span>
      </div>
    </form>
  </section>;
}
