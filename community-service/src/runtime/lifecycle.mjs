/**
 * Orderly shutdown once the listener stopped admitting requests: close the backup gate first so
 * no snapshot can start, stop maintenance, wait for admitted requests, then drain backups again
 * (a request admitted before shutdown may have started one) immediately before closing SQLite.
 */
export async function drainRuntime({ database, active, maintenance, backups }) {
  const gate = backups?.close();
  const maintenanceDone = maintenance?.stop();
  await Promise.allSettled([...active, maintenanceDone, gate]);
  await backups?.drain();
  database.close();
}
