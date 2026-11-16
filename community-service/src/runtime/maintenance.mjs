/** Run hourly transient cleanup without overlap; drain it before closing SQLite. */
export function startMaintenance(service, env, enabled, onFailure = () => console.error('community_maintenance_failed')) {
  let active, stopped = false;
  const timer = setInterval(() => {
    if (stopped || !enabled || active) return;
    active = Promise.resolve().then(() => service.scheduled({}, env))
      .catch(() => onFailure()).finally(() => { active = undefined; });
  }, 3600000);
  timer.unref();
  return {
    async stop() {
      stopped = true;
      clearInterval(timer);
      await active;
    },
  };
}
