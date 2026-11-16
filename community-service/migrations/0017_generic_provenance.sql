DROP TRIGGER guard_release_update;
DROP TRIGGER guard_revision_update;
UPDATE releases SET evidence_json = json_set(evidence_json, '$.media.release.duration.source', 'jellyfin_ticks')
WHERE json_extract(evidence_json, '$.media.release.duration.source') IN ('radarr_catalog_minutes', 'sonarr_catalog_minutes');
UPDATE releases SET evidence_json = json_set(evidence_json, '$.media.identifiers', (
  SELECT json_group_array(CASE WHEN json_extract(value, '$.provenance') LIKE 'radarr.%'
      OR json_extract(value, '$.provenance') LIKE 'sonarr.%'
    THEN json_set(value, '$.provenance', 'source_metadata') ELSE json(value) END ORDER BY key)
  FROM json_each(evidence_json, '$.media.identifiers')))
WHERE EXISTS (SELECT 1 FROM json_each(evidence_json, '$.media.identifiers')
  WHERE json_extract(value, '$.provenance') LIKE 'radarr.%' OR json_extract(value, '$.provenance') LIKE 'sonarr.%');
UPDATE releases SET evidence_json = json_set(evidence_json, '$.media.series_identifiers', (
  SELECT json_group_array(CASE WHEN json_extract(value, '$.provenance') LIKE 'radarr.%'
      OR json_extract(value, '$.provenance') LIKE 'sonarr.%'
    THEN json_set(value, '$.provenance', 'source_metadata') ELSE json(value) END ORDER BY key)
  FROM json_each(evidence_json, '$.media.series_identifiers')))
WHERE EXISTS (SELECT 1 FROM json_each(evidence_json, '$.media.series_identifiers')
  WHERE json_extract(value, '$.provenance') LIKE 'radarr.%' OR json_extract(value, '$.provenance') LIKE 'sonarr.%');
UPDATE contribution_revisions SET summary_json = json_set(summary_json, '$.matching.media.release.duration.source', 'jellyfin_ticks')
WHERE json_extract(summary_json, '$.matching.media.release.duration.source') IN ('radarr_catalog_minutes', 'sonarr_catalog_minutes');
UPDATE contribution_revisions SET summary_json = json_set(summary_json, '$.matching.media.identifiers', (
  SELECT json_group_array(CASE WHEN json_extract(value, '$.provenance') LIKE 'radarr.%'
      OR json_extract(value, '$.provenance') LIKE 'sonarr.%'
    THEN json_set(value, '$.provenance', 'source_metadata') ELSE json(value) END ORDER BY key)
  FROM json_each(summary_json, '$.matching.media.identifiers')))
WHERE EXISTS (SELECT 1 FROM json_each(summary_json, '$.matching.media.identifiers')
  WHERE json_extract(value, '$.provenance') LIKE 'radarr.%' OR json_extract(value, '$.provenance') LIKE 'sonarr.%');
UPDATE contribution_revisions SET summary_json = json_set(summary_json, '$.matching.media.series_identifiers', (
  SELECT json_group_array(CASE WHEN json_extract(value, '$.provenance') LIKE 'radarr.%'
      OR json_extract(value, '$.provenance') LIKE 'sonarr.%'
    THEN json_set(value, '$.provenance', 'source_metadata') ELSE json(value) END ORDER BY key)
  FROM json_each(summary_json, '$.matching.media.series_identifiers')))
WHERE EXISTS (SELECT 1 FROM json_each(summary_json, '$.matching.media.series_identifiers')
  WHERE json_extract(value, '$.provenance') LIKE 'radarr.%' OR json_extract(value, '$.provenance') LIKE 'sonarr.%');
CREATE TRIGGER guard_release_update BEFORE UPDATE ON releases
BEGIN SELECT RAISE(ABORT, 'immutable_release'); END;
CREATE TRIGGER guard_revision_update BEFORE UPDATE ON contribution_revisions
BEGIN SELECT RAISE(ABORT, 'immutable_revision'); END;
