import { runtimeApiBasePath } from "../shared/runtime-urls";
import { jsonApi } from "./api-transport";

interface DockSettings {
  show_in_dock: boolean;
}

export function fetchDockSettings(signal: AbortSignal): Promise<DockSettings | null> {
  return jsonApi(`${runtimeApiBasePath()}/settings/macos/dock`, { signal }, "Unable to load the Dock icon setting.");
}

export function saveDockSettings(showInDock: boolean): Promise<DockSettings> {
  return jsonApi(`${runtimeApiBasePath()}/settings/macos/dock`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ show_in_dock: showInDock }),
  }, "Unable to save the Dock icon setting.");
}
