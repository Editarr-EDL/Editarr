import { db } from './publication';

/** Capture durable publication data, excluding intentionally retained attempt telemetry. */
export async function publicationState() {
  const tables = ['works', 'work_identifiers', 'releases', 'release_work_associations',
    'release_episodes', 'release_fingerprints', 'contributions', 'contribution_revisions',
    'contribution_publications', 'contribution_statistics', 'contribution_parents',
    'artifact_payloads', 'artifact_storage_usage', 'request_nonces'];
  const snapshot: Record<string, unknown> = {};
  for (const table of tables) snapshot[table] = (await db.prepare(`SELECT * FROM ${table}`).all()).results;
  snapshot.operations = (await db.prepare("SELECT * FROM operations WHERE kind = 'publication'").all()).results;
  return snapshot;
}
