import type { JellyfinMediaItem, JellyfinMediaVersion } from "../api/api-media-types";
import { formatRating, MediaDuration } from "./media-display";
import { MediaFiles } from "./media-files";

export function MediaWorkflowHeader({ item, versions, sourceId, onSourceChange, imageUrl, onManageLegacyExports }: {
  item: JellyfinMediaItem;
  versions: JellyfinMediaVersion[];
  sourceId: string | null;
  onSourceChange: (sourceId: string | null) => void;
  imageUrl: string | null;
  onManageLegacyExports?: () => void;
}) {
  const version = versions.find((entry) => entry.id === sourceId);
  const format = version?.media_format || item.media_format;
  const resolution = version?.resolution || item.resolution;
  return <header className="media-workflow-header">
    <div className="media-workflow-heading">
      <div className="detail-poster">{imageUrl ? <img src={imageUrl} alt="" /> : <span aria-hidden="true">{item.name.slice(0, 1).toUpperCase()}</span>}</div>
      <div>
        <p className="eyebrow">{item.item_type}</p>
        <h1>{item.name}</h1>
        <p className="detail-meta">
          {item.year || "Year unavailable"} · Rating {formatRating(item)} · <MediaDuration seconds={version?.duration_seconds ?? item.duration_seconds} />
          {resolution && ` · ${resolution}`}{format && ` · ${format}`}
        </p>
      </div>
    </div>
    {versions.length > 1 && <label className="field media-workflow-version">
      <span>Media version</span>
      <select value={sourceId || ""} onChange={(event) => onSourceChange(event.target.value || null)}>
        {versions.map((entry, index) => <option key={entry.id || `${entry.name || "version"}-${index}`}
          value={entry.id || ""} disabled={!entry.id}>{entry.name || `Version ${index + 1}`}</option>)}
      </select>
    </label>}
    {(item.overview || versions.length > 0 || onManageLegacyExports) && <details className="media-workflow-details">
      <summary>Media files</summary>
      {item.overview && <p>{item.overview}</p>}
      <MediaFiles versions={versions} />
      {onManageLegacyExports && <button type="button" className="btn-secondary" onClick={onManageLegacyExports}>Manage legacy exports</button>}
    </details>}
  </header>;
}
