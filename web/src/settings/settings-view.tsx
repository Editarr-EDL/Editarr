import { useCallback, useEffect, useState } from "react";
import {
  createPathMapping,
  deletePathMapping,
  disconnectJellyfin,
  fetchPathMappings,
  reorderPathMappings,
  resolvePathMapping,
  saveJellyfinSettings,
  testJellyfinSettings,
  updatePathMapping,
} from "../api/api-service";
import type {
  JellyfinSettings,
  PathMapping,
} from "../api/api-media-types";
import { DeploymentSettings } from "./deployment-settings";
import { FiltersPage, type FilterTab } from "./filters/filters-page";
import { RestartEditarr } from "./restart-editarr";
import { ModelStorage } from "./model-storage";
import { DiagnosticsPanel } from "./diagnostics-panel";
import { About } from "./about";
import { BackupSettings } from "./backup-settings";
import { CommunitySettings } from "./community/community-settings";
import { usePageTitle } from "../shared/display";
import { ModalDialog } from "../ui/ModalDialog";
import { useCan, usePrincipal } from "../auth/auth-gate";
import { maySeeSettings } from "../auth/route-access";

export type SettingsSection = "connections" | "filters" | "models" | "general" | "users" | "profile" | "audit";
export function settingsSectionTitle(section: SettingsSection): string {
  if (section === "users") return "Users";
  if (section === "profile") return "My Profile";
  if (section === "filters") return "Filters";
  if (section === "models") return "Model Storage";
  if (section === "general") return "General";
  if (section === "audit") return "Audit History";
  return "Connections";
}

/** Render the selected settings section and manage connection and path mapping edits. */
export function SettingsView({ section, filterTab, settings, onSaved }: { section: SettingsSection; filterTab?: FilterTab; settings: JellyfinSettings | null; onSaved: () => Promise<void> }) {
  usePageTitle(`Settings — ${settingsSectionTitle(section)}`);
  const principal = usePrincipal();
  const mayTestConnection = useCan("connections.test");
  const mayEditConnection = useCan("connections.edit");
  const mayViewCommunity = useCan("community.identity.view.own");
  const mayDisconnect = useCan("connections.disconnect");
  const mayCreateMapping = useCan("path_mappings.create");
  const mayEditMapping = useCan("path_mappings.update");
  const mayDeleteMapping = useCan("path_mappings.delete");
  const mayReorderMappings = useCan("path_mappings.reorder");
  const mayTestMapping = useCan("path_mappings.test");
  const maySeePaths = useCan("media.paths.view");
  const mayViewMappings = useCan("path_mappings.view") && maySeePaths;
  const mayViewDeployment = useCan("deployment.view");
  const mayViewLogs = useCan("logs.view");
  const mayEditLogs = useCan("logs.settings.edit");
  const mayRestart = useCan("system.restart");
  const mayCreateBackup = useCan("backups.create");
  const mayScheduleBackup = useCan("backups.schedule.edit");
  const mayViewBackupStatus = useCan("backups.status.view");
  const mayViewVersion = useCan("system.version.view");
  const [serverURL, setServerURL] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [feedback, setFeedback] = useState<{ section: SettingsSection; message: string | null } | null>(null);
  const status = feedback?.section === section ? feedback.message : null;
  const setStatus = useCallback((message: string | null) => {
    setFeedback({ section, message });
  }, [section]);
  const [working, setWorking] = useState<"test" | "save" | "disconnect" | "mapping" | "resolve" | "edit" | "delete" | "reorder" | "undo" | null>(null);
  const [showDisconnectJellyfin, setShowDisconnectJellyfin] = useState(false);
  const [mappings, setMappings] = useState<PathMapping[]>([]);
  const [remotePath, setRemotePath] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [resolutionPath, setResolutionPath] = useState("");
  const [resolution, setResolution] = useState<string | null>(null);
  const [editingMapping, setEditingMapping] = useState<PathMapping | null>(null);
  const [editingRemotePath, setEditingRemotePath] = useState("");
  const [editingLocalPath, setEditingLocalPath] = useState("");
  const [pendingDeletion, setPendingDeletion] = useState<PathMapping | null>(null);
  const [undoDeletion, setUndoDeletion] = useState<{ mapping: PathMapping; mappingIds: number[] } | null>(null);
  const [deploymentDirty, setDeploymentDirty] = useState(false);

  useEffect(() => { setServerURL(settings?.server_url || ""); }, [settings?.server_url]);
  const refreshMappings = useCallback(async () => {
    try { setMappings(await fetchPathMappings()); }
    catch (error) { setStatus(error instanceof Error ? error.message : "Unable to load path mappings."); }
  }, [setStatus]);
  useEffect(() => {
    if (section === "general" && mayViewMappings) void refreshMappings();
  }, [refreshMappings, section, mayViewMappings]);

  const connectionPayload = () => ({
    server_url: serverURL.trim(),
    ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
  });
  const testConnection = async () => {
    setWorking("test"); setStatus(null);
    try { await testJellyfinSettings(connectionPayload()); setStatus("Connection succeeded. Save to use this server."); }
    catch (error) { setStatus(error instanceof Error ? error.message : "Unable to connect to Jellyfin."); }
    finally { setWorking(null); }
  };
  const saveConnection = async () => {
    setWorking("save"); setStatus(null);
    try {
      await saveJellyfinSettings(connectionPayload());
      setApiKey("");
      setStatus("Jellyfin settings saved.");
      await onSaved();
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to save Jellyfin settings."); }
    finally { setWorking(null); }
  };
  const disconnectConnection = async () => {
    setWorking("disconnect"); setStatus(null);
    try {
      await disconnectJellyfin();
      setServerURL("");
      setApiKey("");
      setShowDisconnectJellyfin(false);
      await onSaved();
      setStatus("Jellyfin disconnected.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to disconnect Jellyfin.");
    } finally { setWorking(null); }
  };
  const addMapping = async (event: React.FormEvent) => {
    event.preventDefault(); setWorking("mapping"); setStatus(null);
    try {
      await createPathMapping(remotePath, localPath);
      setRemotePath(""); setLocalPath("");
      if (mayViewMappings) await refreshMappings();
      setUndoDeletion(null);
      setStatus("Path mapping added.");
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to add path mapping."); }
    finally { setWorking(null); }
  };
  const startEditing = (mapping: PathMapping) => {
    setEditingMapping(mapping);
    setEditingRemotePath(mapping.remote_path);
    setEditingLocalPath(mapping.local_path);
    setStatus(null);
  };
  const saveMapping = async () => {
    if (!editingMapping) return;
    setWorking("edit"); setStatus(null);
    try {
      const updated = await updatePathMapping(editingMapping.id, editingRemotePath, editingLocalPath);
      setMappings((current) => current.map((mapping) => mapping.id === updated.id ? updated : mapping));
      setEditingMapping(null);
      setUndoDeletion(null);
      setStatus("Path mapping updated.");
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to update path mapping."); }
    finally { setWorking(null); }
  };
  const moveMapping = async (mapping: PathMapping, offset: -1 | 1) => {
    const currentIndex = mappings.findIndex((candidate) => candidate.id === mapping.id);
    const targetIndex = currentIndex + offset;
    if (currentIndex < 0 || targetIndex < 0 || targetIndex >= mappings.length) return;
    const reorderedIds = mappings.map((candidate) => candidate.id);
    [reorderedIds[currentIndex], reorderedIds[targetIndex]] = [reorderedIds[targetIndex], reorderedIds[currentIndex]];
    setWorking("reorder"); setStatus(null);
    try {
      setMappings(await reorderPathMappings(reorderedIds));
      setUndoDeletion(null);
      setStatus("Path mapping order updated.");
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to reorder path mappings."); }
    finally { setWorking(null); }
  };
  const deleteMapping = async () => {
    if (!pendingDeletion) return;
    const mapping = pendingDeletion;
    const mappingIds = mappings.map((candidate) => candidate.id);
    setWorking("delete"); setStatus(null);
    try {
      await deletePathMapping(mapping.id);
      setMappings((current) => current.filter((candidate) => candidate.id !== mapping.id));
      setPendingDeletion(null);
      setUndoDeletion({ mapping, mappingIds });
      setStatus("Path mapping deleted.");
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to delete path mapping."); }
    finally { setWorking(null); }
  };
  const undoDelete = async () => {
    if (!undoDeletion) return;
    setWorking("undo"); setStatus(null);
    try {
      const restored = await createPathMapping(undoDeletion.mapping.remote_path, undoDeletion.mapping.local_path);
      const restoredOrder = undoDeletion.mappingIds.map((id) => id === undoDeletion.mapping.id ? restored.id : id);
      setMappings(await reorderPathMappings(restoredOrder));
      setUndoDeletion(null);
      setStatus("Path mapping restored.");
    } catch (error) { setStatus(error instanceof Error ? error.message : "Unable to restore path mapping."); }
    finally { setWorking(null); }
  };
  const testResolution = async () => {
    setWorking("resolve"); setResolution(null);
    try {
      const result = await resolvePathMapping(resolutionPath);
      setResolution(result.mapping_id ? `Maps to ${result.local_path}` : `No mapping applies; scanner will use ${result.local_path}`);
    } catch (error) { setResolution(error instanceof Error ? error.message : "Unable to resolve path."); }
    finally { setWorking(null); }
  };


  const mappingDirty = Boolean(
    remotePath
    || localPath
    || (editingMapping && (
      editingRemotePath !== editingMapping.remote_path
      || editingLocalPath !== editingMapping.local_path
    ))
  );
  const generalSettingsDirty = deploymentDirty || mappingDirty;

  if (!principal || !maySeeSettings(principal, section)) return <div className="content-page"><h1>Page unavailable</h1></div>;

  return <div className="content-page settings-page">
    <header className={`page-header ${section === "general" ? "general-settings-page-header" : ""}`}><div><p className="eyebrow">Settings</p><h1>{settingsSectionTitle(section)}</h1><p>{section === "connections" ? "Connect Editarr to your media sources." : section === "filters" ? "Choose what Editarr selects for you when you review a scan." : ""}</p></div>{section === "general" && mayRestart && <div className="general-restart-action-slot" data-restart-editarr-slot="true"><RestartEditarr hasUnsavedChanges={generalSettingsDirty} /></div>}</header>
    {section === "connections" && <>
    {mayViewCommunity && <CommunitySettings />}
    <section className="settings-card" aria-labelledby="jellyfin-heading">
      <div className="section-heading"><div><h2 id="jellyfin-heading">Jellyfin</h2><p>Editarr uses this server to browse media, metadata, and artwork.</p></div>{settings?.api_key_configured && <span className="configured-badge">API key configured</span>}</div>
      {mayEditConnection || mayTestConnection ? <>
      <label className="field"><span>Server address</span><input type="url" value={serverURL} onChange={(event) => setServerURL(event.target.value)} placeholder="https://jellyfin.example.com" autoComplete="url" /></label>
      <label className="field"><span>API key</span><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={settings?.api_key_configured ? "Leave blank to keep the saved key" : "Paste Jellyfin API key"} autoComplete="off" /></label>
      </> : <dl><div><dt>Server address</dt><dd>{settings?.server_url || "Not configured"}</dd></div></dl>}
      <div className="button-row">{mayTestConnection && <button className="btn-secondary" onClick={() => void testConnection()} disabled={working !== null}>{working === "test" ? "Testing…" : "Test connection"}</button>}{mayEditConnection && <button className="btn-primary" onClick={() => void saveConnection()} disabled={working !== null}>{working === "save" ? "Saving…" : "Save"}</button>}{mayDisconnect && settings?.api_key_configured && <button className="danger-button" onClick={() => setShowDisconnectJellyfin(true)} disabled={working !== null}>Disconnect</button>}</div>
      {status && <p className="form-status" role="status">{status}</p>}
    </section>
    </>}
    {section === "filters" && <FiltersPage tab={filterTab} />}
    {section === "models" && <ModelStorage />}
    {section === "general" && <>
    {(mayCreateBackup || mayScheduleBackup || mayViewBackupStatus) && <BackupSettings />}
    {mayViewDeployment && <DeploymentSettings onDirtyChange={setDeploymentDirty} />}
    {(mayViewLogs || mayEditLogs) && <DiagnosticsPanel />}
    {mayViewVersion && <About />}
    {(mayViewMappings || (maySeePaths && (mayCreateMapping || mayTestMapping))) && <>
    <section className="settings-card" aria-labelledby="mapping-heading">
      <div className="section-heading"><div><h2 id="mapping-heading">Base path mappings</h2><p>Optionally translate a Jellyfin server path to the local path visible to the Editarr scanner.</p></div></div>
      {mayCreateMapping && maySeePaths && <form className="mapping-form" onSubmit={(event) => void addMapping(event)}>
        <label className="field"><span>Remote path</span><input value={remotePath} onChange={(event) => setRemotePath(event.target.value)} placeholder="/media/movies" required /></label>
        <label className="field"><span>Local path</span><input value={localPath} onChange={(event) => setLocalPath(event.target.value)} placeholder="/mnt/media/movies" required /></label>
        <button className="btn-secondary" type="submit" disabled={working !== null}>{working === "mapping" ? "Adding…" : "Add mapping"}</button>
      </form>}
      {mayViewMappings && (mappings.length ? <div className="mapping-table-wrap" role="region" aria-label="Path mappings table" tabIndex={0}><table><thead><tr><th>Remote path</th><th>Local path</th><th><span className="visually-hidden">Actions</span></th></tr></thead><tbody>{mappings.map((mapping, index) => <tr key={mapping.id}>{editingMapping?.id === mapping.id ? <>
        <td><input aria-label={`Remote path for mapping ${mapping.id}`} value={editingRemotePath} onChange={(event) => setEditingRemotePath(event.target.value)} required /></td>
        <td><input aria-label={`Local path for mapping ${mapping.id}`} value={editingLocalPath} onChange={(event) => setEditingLocalPath(event.target.value)} required /></td>
        <td><div className="mapping-actions"><button className="btn-primary compact-button" onClick={() => void saveMapping()} disabled={working !== null || !editingRemotePath.trim() || !editingLocalPath.trim()}>Save mapping</button><button className="btn-secondary compact-button" onClick={() => setEditingMapping(null)} disabled={working !== null}>Cancel</button></div></td>
      </> : <>
        <td>{mapping.remote_path}</td><td>{mapping.local_path}</td><td><div className="mapping-actions">{mayReorderMappings && <><button className="icon-button" onClick={() => void moveMapping(mapping, -1)} disabled={working !== null || index === 0} aria-label={`Move ${mapping.remote_path} up`} title="Move up">↑</button><button className="icon-button" onClick={() => void moveMapping(mapping, 1)} disabled={working !== null || index === mappings.length - 1} aria-label={`Move ${mapping.remote_path} down`} title="Move down">↓</button></>}{mayEditMapping && <button className="btn-secondary compact-button" onClick={() => startEditing(mapping)} disabled={working !== null}>Edit</button>}{mayDeleteMapping && <button className="danger-button compact-button" onClick={() => setPendingDeletion(mapping)} disabled={working !== null}>Delete</button>}</div></td>
      </>}</tr>)}</tbody></table></div> : <p className="muted-copy">No mappings configured. The scanner will use the Jellyfin path unchanged.</p>)}
      {undoDeletion && mayCreateMapping && mayReorderMappings && mayViewMappings && <div className="undo-notice" role="status"><span>Path mapping deleted.</span><button className="btn-secondary compact-button" onClick={() => void undoDelete()} disabled={working !== null}>Undo</button></div>}
      {mayTestMapping && maySeePaths && <div className="resolution-test"><label className="field"><span>Test a remote path</span><input value={resolutionPath} onChange={(event) => setResolutionPath(event.target.value)} placeholder="/media/movies/example.mkv" /></label><button className="btn-secondary" onClick={() => void testResolution()} disabled={!resolutionPath.trim() || working !== null}>{working === "resolve" ? "Testing…" : "Test resolution"}</button></div>}
      {resolution && <p className="form-status" role="status">{resolution}</p>}
    </section>
    {status && <p className="form-status" role="status">{status}</p>}
    {pendingDeletion && <ModalDialog labelledBy="delete-mapping-heading" className="confirmation-dialog" onClose={working === null ? () => setPendingDeletion(null) : undefined}><h2 id="delete-mapping-heading">Delete path mapping?</h2><p><strong>{pendingDeletion.remote_path}</strong> will no longer be translated to <strong>{pendingDeletion.local_path}</strong>.</p><div className="button-row"><button className="btn-secondary" onClick={() => setPendingDeletion(null)} disabled={working !== null}>Cancel</button><button className="danger-button" onClick={() => void deleteMapping()} disabled={working !== null}>{working === "delete" ? "Deleting…" : "Delete mapping"}</button></div></ModalDialog>}
    </>}
    </>}
    {showDisconnectJellyfin && <ModalDialog labelledBy="disconnect-jellyfin-heading" className="confirmation-dialog" onClose={working === null ? () => setShowDisconnectJellyfin(false) : undefined}><h2 id="disconnect-jellyfin-heading">Disconnect Jellyfin?</h2><p>This clears the saved address and API key and removes cached Jellyfin catalog data. Existing local scans remain available.</p><div className="button-row"><button className="btn-secondary" onClick={() => setShowDisconnectJellyfin(false)} disabled={working !== null}>Cancel</button><button className="danger-button" onClick={() => void disconnectConnection()} disabled={working !== null}>{working === "disconnect" ? "Disconnecting…" : "Disconnect Jellyfin"}</button></div></ModalDialog>}
  </div>;
}
