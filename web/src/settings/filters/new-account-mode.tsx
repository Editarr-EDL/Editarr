import { useEffect, useState } from "react";

import { readDefaultFilterMode, saveDefaultFilterMode, type FilterMode } from "../../api/api-user-access";
import { ModeChoice, type ModeOption } from "./mode-choice";

const modeOptions: ModeOption[] = [
  { value: "live", title: "Keep them in sync", body: "Your changes here reach them automatically, except where they've made their own choice." },
  { value: "copy", title: "Give them their own copy", body: "They start from today's defaults. Later changes here won't reach them." },
];
const modeSaved: Record<FilterMode, string> = { live: "New accounts will keep in sync.", copy: "New accounts will get their own copy." };

/** The mode new accounts start in. Existing accounts keep their own. */
export function NewAccountMode() {
  const [mode, setMode] = useState<FilterMode | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; failed: boolean } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void readDefaultFilterMode(controller.signal).then(result => setMode(result.mode)).catch(reason => {
      if (!controller.signal.aborted) setMessage({ text: reason instanceof Error ? reason.message : "Unable to load the mode for new accounts.", failed: true });
    });
    return () => controller.abort();
  }, []);

  async function choose(next: FilterMode) {
    setBusy(true); setMessage(null);
    try {
      const result = await saveDefaultFilterMode(next);
      setMode(result.mode); setMessage({ text: modeSaved[result.mode], failed: false });
    } catch (reason) { setMessage({ text: reason instanceof Error ? reason.message : "Unable to save the mode for new accounts.", failed: true }); }
    finally { setBusy(false); }
  }

  return <ModeChoice heading="For new accounts" options={modeOptions} value={mode} disabled={mode === null || busy}
    description="How server defaults reach accounts created from now on. Existing accounts keep their current choice, and people can change theirs on the My filters tab."
    note={message && <p className={message.failed ? "form-error" : "form-status"} role={message.failed ? "alert" : "status"}>{message.text}</p>}
    onChoose={choose} />;
}
