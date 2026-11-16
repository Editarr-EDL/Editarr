import { useState, type FormEvent } from "react";
import type { MediaScope, MediaSource, ScopeChoice } from "../../api/api-user-access";

export function MediaScopeEditor({ scope, allowInherit, onSave, busy = false }: {
  scope: MediaScope; allowInherit: boolean; busy?: boolean; onSave: (choice: ScopeChoice) => void;
}) {
  const [mode, setMode] = useState(scope.inherited && allowInherit ? "inherit" : scope.all_sources ? "all" : "selected");
  const [sources, setSources] = useState<MediaSource[]>(scope.sources);
  const [library, setLibrary] = useState("");
  const [error, setError] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    onSave(mode === "inherit" ? { inherit: true } : { all_sources: mode === "all", sources: mode === "all" ? [] : sources });
  }
  return <form onSubmit={submit}>
    <p>{scope.inherited ? "Inherited" : "Explicit"}. Effective sources: {scope.all_sources ? "All sources" : scope.sources.map(source => `Jellyfin ${source.id}`).join(", ") || "None"}.</p>
    <label className="field"><span>Media scope</span><select value={mode} disabled={busy} onChange={event => setMode(event.target.value)}>{allowInherit && <option value="inherit">Inherit live default</option>}<option value="all">All sources</option><option value="selected">Selected sources</option></select></label>
    {mode === "selected" && <fieldset disabled={busy}><legend>Allowed sources</legend>
      <label className="field"><span>Jellyfin library ID</span><input value={library} onChange={event => setLibrary(event.target.value)} /></label>
      <button type="button" className="btn-secondary" onClick={() => {
        const id = library.replace(/-/g, "").toLowerCase();
        if (!/^[0-9a-f]{32}$/.test(id)) { setError("Enter a Jellyfin library GUID."); return; }
        if (!sources.some(source => source.id === id)) setSources([...sources, { kind: "jellyfin_library", id }]);
        setLibrary(""); setError("");
      }}>Add library</button>
      <ul>{sources.map(source => <li key={source.id}>{source.id} <button type="button" className="btn-secondary" aria-label={`Remove library ${source.id}`} onClick={() => setSources(sources.filter(entry => entry !== source))}>Remove</button></li>)}</ul>
    </fieldset>}
    {error && <p role="alert">{error}</p>}
    <button className="btn-secondary" disabled={busy} type="submit">Save media scope</button>
  </form>;
}