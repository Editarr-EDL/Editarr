export type BackupFrequency = 'hourly' | 'every_6h' | 'daily' | 'weekly';
export interface ScheduleConfig { enabled: number | boolean; frequency: BackupFrequency; at_utc: string }

const HOUR = 3_600_000, DAY = 24 * HOUR;
const periods: Record<BackupFrequency, number> = { hourly: HOUR, every_6h: 6 * HOUR, daily: DAY, weekly: 7 * DAY };
/** 1970-01-04 was the first Sunday after the epoch; weekly slots fall on Sundays (UTC). */
const FIRST_SUNDAY = 3 * DAY;

function anchor(config: ScheduleConfig): number {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(config.at_utc);
  if (!match) throw new Error('invalid_backup_schedule');
  const offset = (Number(match[1]) * 60 + Number(match[2])) * 60_000;
  return config.frequency === 'hourly' ? offset % HOUR : config.frequency === 'weekly' ? FIRST_SUNDAY + offset : offset;
}

/**
 * The most recent schedule slot at or before `nowMs`. Slots: hourly at the minute of `at_utc`,
 * every_6h at `at_utc` plus multiples of 6 hours, daily at `at_utc`, weekly on Sundays at `at_utc`.
 */
export function latestBackupSlot(config: ScheduleConfig, nowMs: number): number {
  const period = periods[config.frequency], start = anchor(config);
  return start + Math.floor((nowMs - start) / period) * period;
}

/** The next scheduled backup strictly after `nowMs`, or null when scheduled backups are disabled. */
export function nextBackupAt(config: ScheduleConfig, nowMs: number): number | null {
  if (!config.enabled) return null;
  return latestBackupSlot(config, nowMs) + periods[config.frequency];
}

/**
 * Whether a scheduled run is due: the latest slot was reached after the configuration was last
 * saved (so enabling never fires a slot that already passed) and no scheduled run started since it.
 * A slot missed during downtime runs once on the next check.
 */
export function backupDue(config: ScheduleConfig & { updated_at: number }, lastScheduledStart: number | null, nowMs: number): boolean {
  if (!config.enabled) return false;
  const slot = latestBackupSlot(config, nowMs);
  return slot >= config.updated_at && (lastScheduledStart === null || lastScheduledStart < slot);
}
