/**
 * The capability catalog the server may grant (`editarr-capabilities-9`).
 * `tests/test_web_capability_contract.py` keeps this list equal to the backend catalog.
 */
export const CAPABILITIES = [
  "profile.view.own", "profile.edit.own", "accounts.list",
  "accounts.jellyfin.import", "accounts.jellyfin.sync", "accounts.jellyfin.unblock",
  "accounts.profile.edit.other", "accounts.disable.other", "accounts.enable.other",
  "accounts.delete.other", "accounts.sessions.revoke.other",
  "accounts.link.own", "accounts.link.other", "accounts.admins.manage",
  "media_access.defaults.edit",
  "media_access.user.edit", "api_keys.create.own",
  "api_keys.rotate.own", "api_keys.reveal.own", "api_keys.revoke.own", "api_keys.revoke.other",
  "api_keys.issue.other", "api_keys.reveal.other", "filters.mode.choose.own", "ownership.transfer",
  "roles.defaults.edit", "roles.assign", "permissions.overrides.edit", "media.browse",
  "media.paths.view", "media.identity.edit", "scans.findings.read",
  "scans.start", "scans.rename.own", "scans.rename.other",
  "scans.delete.own", "scans.delete.other", "scan_jobs.view.own", "scan_jobs.view.other",
  "scan_jobs.cancel.own", "scan_jobs.cancel.other", "scan_jobs.resume.own",
  "scan_jobs.resume.other", "scan_jobs.reorder", "reviews.create.own", "reviews.view.own",
  "reviews.save.own", "reviews.rename.own", "reviews.duplicate.own", "reviews.delete.own",
  "reviews.view.other", "reviews.copy.other", "reviews.edit.other", "reviews.delete.other",
  "reviews.export.other", "reviews.evidence.edit.own", "reviews.evidence.edit.other",
  "reviews.filters.edit.own", "reviews.filters.edit.other", "reviews.cues.edit.own",
  "reviews.cues.edit.other", "reviews.metadata.edit.own", "reviews.metadata.edit.other",
  "reviews.timing.edit.own", "reviews.timing.edit.other", "reviews.credits.edit.own",
  "reviews.credits.edit.other", "reviews.preview.own", "reviews.preview.other",
  "reviews.import.upload", "exports.personal.download.own",
  "exports.personal.publish.own", "exports.personal.delete.own", "exports.personal.delete.other",
  "exports.default.publish", "exports.default.delete", "filters.preferences.edit.own",
  "filters.preferences.edit.other", "filters.instance_defaults.edit", "filters.instance_mode.edit",
  "filters.mode.assign.other", "filters.policy.enforce", "community.search", "community.download",
  "community.feedback.submit", "community.publish.own_review", "community.publish.scan_evidence",
  "community.publish.other_review", "community.publications.check.own",
  "community.publications.check.other", "community.publications.retry.own",
  "community.publications.retry.other", "community.publications.retract.own",
  "community.publications.retract.other", "community.identity.view.own",
  "community.identity.view.other", "community.identity.enroll.own",
  "community.identity.enroll.other", "community.identity.profile.own",
  "community.identity.profile.other", "community.identity.disconnect.own",
  "community.identity.disconnect.other", "community.identity.keys.own",
  "community.identity.keys.other", "community.receipts.recover.own",
  "community.receipts.recover.other", "connections.view", "connections.test", "connections.edit",
  "connections.disconnect", "models.inventory.view", "models.download", "models.progress.view.own",
  "models.download.cancel", "models.delete", "path_mappings.view", "path_mappings.test",
  "path_mappings.create", "path_mappings.update", "path_mappings.delete", "path_mappings.reorder",
  "deployment.view", "deployment.edit", "logs.view", "logs.settings.edit", "system.restart",
  "system.version.view", "backups.create", "backups.schedule.edit", "backups.status.view",
  "backups.download", "backups.delete", "audit.read", "archive.view",
] as const;

export type Capability = typeof CAPABILITIES[number];

const KNOWN: ReadonlySet<string> = new Set(CAPABILITIES);

/** Keep only capabilities this client knows; an unknown grant never shows a control. */
export function knownCapabilities(values: readonly string[]): ReadonlySet<Capability> {
  return new Set(values.filter((value): value is Capability => KNOWN.has(value)));
}
