/** Run hourly transient cleanup without overlap; drain it before closing SQLite. */
export function startMaintenance(service, env, enabled, onFailure = () => console.error('community_maintenance_failed'),
  backups = null) {
  let active, stopped = false;
  const timer = setInterval(() => {
    if (stopped || !enabled || active) return;
    active = Promise.resolve().then(() => service.scheduled({}, env))
      .catch(() => onFailure()).finally(() => { active = undefined; });
  }, 3600000);
  timer.unref();
  // Online backups: check the UI-configured schedule every minute; `backups` is single-flight itself.
  let backing;
  const backupTimer = backups && setInterval(() => {
    if (stopped || !enabled || backing) return;
    backing = Promise.resolve().then(() => backups.tick(env, Date.now()))
      .catch(() => console.error('community_backup_failed')).finally(() => { backing = undefined; });
  }, 60000);
  backupTimer?.unref();
  return {
    async stop() {
      stopped = true;
      clearInterval(timer);
      clearInterval(backupTimer);
      await Promise.all([active, backing, backups?.drain()]);
    },
  };
}
