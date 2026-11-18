import type { JellyfinMediaVersion } from "../api/api-media-types";
import { ExportBadges } from "../review/export-status";
import { formatBytes } from "../shared/display";
import { MediaDuration } from "./media-display";

export function MediaFiles({ versions }: { versions: JellyfinMediaVersion[] }) {
  if (!versions.length) return null;
  return <section className="settings-card media-versions">
    <div className="section-heading"><div><h2>Files</h2></div></div>
    <div className="library-table-wrap" role="region" aria-label="Media files" tabIndex={0}>
      <table className="library-table">
        <thead>
          <tr>
            <th scope="col">File name</th>
            <th scope="col">Resolution</th>
            <th scope="col">Format</th>
            <th scope="col">Audio Tracks</th>
            <th scope="col">Length</th>
            <th scope="col">Size</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((version, index) => <tr key={version.id || `${version.name || "file"}-${index}`}>
            <td>
              {version.file_name ?? "—"}
              <span className="version-edl-badge"><ExportBadges status={version.exports} /></span>
            </td>
            <td>{version.resolution || "—"}</td>
            <td>{version.media_format || version.container?.toUpperCase() || "—"}</td>
            <td>{version.audio_track_count ?? "—"}</td>
            <td><MediaDuration seconds={version.duration_seconds} /></td>
            <td>{formatBytes(version.size_bytes, { unit: "gb", unavailable: "—" })}</td>
          </tr>)}
        </tbody>
      </table>
    </div>
  </section>;
}
