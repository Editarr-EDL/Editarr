import { useEffect, useState } from "react";
import { version as frontendRelease } from "../../package.json";
import { fetchVersionInformation } from "../api/api-service";
import type { VersionInformation } from "../api/api-system-types";

export function About() {
  const [version, setVersion] = useState<VersionInformation | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    void fetchVersionInformation()
      .then((value) => { if (active) setVersion(value); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);

  return <section className="settings-card" aria-labelledby="about-heading">
    <h2 id="about-heading">About Editarr</h2>
    {error && <p>Version information is unavailable.</p>}
    {!error && !version && <p>Loading version information…</p>}
    {version && version.release !== frontendRelease &&
      <p role="alert">Frontend {frontendRelease} does not match server {version.release}. Reload after updating Editarr.</p>}
    {version && <dl className="version-details">
      <dt>Release</dt><dd>{version.release}</dd>
      <dt>Frontend</dt><dd>{frontendRelease}</dd>
      <dt>Build</dt><dd>{version.build_revision ? `${version.build_revision.slice(0, 12)}${version.build_dirty === true ? " (dirty)" : version.build_dirty === null ? " (state unknown)" : ""}` : "Unknown"}</dd>
      <dt>Scanner behavior</dt><dd>{version.scanner_behavior}</dd>
      <dt>Database schema</dt><dd>{version.database_schema}</dd>
      <dt>Local API</dt><dd>{version.local_api}</dd>
    </dl>}
  </section>;
}