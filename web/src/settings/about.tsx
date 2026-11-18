import { useEffect, useState } from "react";
import { version as frontendRelease } from "../../package.json";
import { fetchVersionInformation } from "../api/api-service";
import type { VersionInformation } from "../api/api-system-types";
import { GeneralSettingsSection } from "./general-settings-section";

export function About({ initiallyOpen }: { initiallyOpen?: boolean }) {
  const [version, setVersion] = useState<VersionInformation | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    void fetchVersionInformation()
      .then((value) => { if (active) setVersion(value); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);

  return <GeneralSettingsSection id="about" title="About Editarr"
    value={version?.release ?? (error ? "Unavailable" : "Loading…")} initiallyOpen={initiallyOpen}>
    {error && <p>Version information is unavailable.</p>}
    {!error && !version && <p>Loading version information…</p>}
    {version && version.release !== frontendRelease &&
      <p role="alert">Frontend {frontendRelease} does not match server {version.release}. Reload after updating Editarr.</p>}
    {version && <>
      <dl className="general-settings-facts general-about-facts">
        <div><dt>Release</dt><dd>{version.release}</dd></div>
        <div><dt>Build</dt><dd>{version.build_revision ? `${version.build_revision.slice(0, 12)}${version.build_dirty === true ? " (dirty)" : version.build_dirty === null ? " (state unknown)" : ""}` : "Unknown"}</dd></div>
      </dl>
      <details className="general-settings-more">
        <summary>Build details</summary>
        <dl className="general-settings-facts general-settings-more-content">
          <div><dt>Frontend</dt><dd>{frontendRelease}</dd></div>
          <div><dt>Scanner behavior</dt><dd>{version.scanner_behavior}</dd></div>
          <div><dt>Database schema</dt><dd>{version.database_schema}</dd></div>
          <div><dt>Local API</dt><dd>{version.local_api}</dd></div>
        </dl>
      </details>
    </>}
  </GeneralSettingsSection>;
}
