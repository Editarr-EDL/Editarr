import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BackupScheduleSettings } from "./backup-schedule-settings";
import * as api from "../api/api-backup-schedule";

const auth = vi.hoisted(() => ({ authenticated: true, edit: true, view: true }));
vi.mock("../auth/auth-gate", () => ({ useCan: (capability: string) => auth.authenticated && (capability === "backups.status.view" ? auth.view : auth.edit) }));
vi.mock("../api/api-backup-schedule", () => ({
  fetchBackupSchedule: vi.fn(), saveBackupSchedule: vi.fn(), disableBackupSchedule: vi.fn(),
}));

const empty: api.BackupScheduleStatus = {
  config: null, next_run: null, last_attempt: null, last_success: null,
  outcome: "idle", error: null, retained_count: 0, restore_hold: false, notice: null,
};
const config: api.BackupScheduleConfig = {
  enabled: true, interval_hours: 24, keep_count: 7, destination: "/private/backups",
  kind: "full", protection: "plain", passphrase_file: null,
};

beforeEach(() => { auth.authenticated = true; auth.edit = true; auth.view = true; vi.mocked(api.fetchBackupSchedule).mockResolvedValue(empty); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });

async function fillSchedule() {
  await waitFor(() => expect(screen.getByLabelText("Enable scheduled backups")).toBeEnabled());
  fireEvent.click(screen.getByLabelText("Enable scheduled backups"));
  fireEvent.change(screen.getByLabelText("Interval (hours)"), { target: { value: "24" } });
  fireEvent.change(screen.getByLabelText("Backups to keep"), { target: { value: "7" } });
  fireEvent.change(screen.getByLabelText("Backup destination"), { target: { value: "/private/backups" } });
}

it("never configures or enables on mount and requires explicit interval/count", async () => {
  render(<BackupScheduleSettings />);
  const toggle = await screen.findByLabelText("Enable scheduled backups");
  expect(toggle).not.toBeChecked();
  expect(screen.queryByRole("spinbutton", { name: "Interval (hours)" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Manage schedule" }));
  expect(await screen.findByText("Disabled")).toBeVisible();
  expect(screen.getByLabelText("Interval (hours)")).toHaveValue(null);
  expect(screen.getByLabelText("Backups to keep")).toHaveValue(null);
  expect(screen.getByRole("button", { name: "Save schedule" })).toBeDisabled();
  expect(api.saveBackupSchedule).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Protected passphrase file")).toBeNull();
});

it("saves explicit consent and only a credential path, never a passphrase", async () => {
  vi.mocked(api.saveBackupSchedule).mockResolvedValue({ ...empty, config: { ...config, protection: "age-scrypt", passphrase_file: "/private/credential" } });
  render(<BackupScheduleSettings />);
  await fillSchedule();
  fireEvent.click(screen.getByLabelText("Encrypt scheduled backups"));
  expect(screen.getByRole("button", { name: "Save schedule" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Protected passphrase file"), { target: { value: "/private/credential" } });
  fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  await waitFor(() => expect(api.saveBackupSchedule).toHaveBeenCalledWith({
    ...config, protection: "age-scrypt", passphrase_file: "/private/credential",
  }));
  expect(await screen.findByText("Backup schedule saved.")).toBeVisible();
  expect(document.querySelector('input[type="password"]')).toBeNull();
});

it("keeps unsaved edits across refresh and shows durable failures", async () => {
  render(<BackupScheduleSettings />);
  await fillSchedule();
  vi.mocked(api.fetchBackupSchedule).mockResolvedValue({ ...empty, config,
    outcome: "failed", error: "credential_unavailable", last_success: 1900000000 });
  fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  expect(await screen.findByText(/No unencrypted fallback/)).toBeVisible();
  expect(screen.getByLabelText("Interval (hours)")).toHaveValue(24);
  expect(screen.getByLabelText("Enable scheduled backups")).toBeChecked();
  expect(api.saveBackupSchedule).not.toHaveBeenCalled();
});

it("disables an existing schedule without requiring a usable credential", async () => {
  vi.mocked(api.fetchBackupSchedule).mockResolvedValue({ ...empty, config });
  vi.mocked(api.disableBackupSchedule).mockResolvedValue({ ...empty, config: { ...config, enabled: false } });
  render(<BackupScheduleSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Manage schedule" }));
  fireEvent.click(await screen.findByRole("button", { name: "Disable schedule" }));
  await waitFor(() => expect(api.disableBackupSchedule).toHaveBeenCalledOnce());
  expect(await screen.findByText("Scheduled backups disabled.")).toBeVisible();
});

it("does not render or fetch without the schedule capability", () => {
  auth.authenticated = false;
  render(<BackupScheduleSettings />);
  expect(screen.queryByRole("region", { name: "Scheduled backups" })).toBeNull();
  expect(api.fetchBackupSchedule).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Enable scheduled backups")).toBeNull();
});

it("blocks changes during a running backup and displays request failures", async () => {
  vi.mocked(api.fetchBackupSchedule).mockResolvedValue({ ...empty, config, outcome: "running" });
  render(<BackupScheduleSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Manage schedule" }));
  expect(await screen.findByText("Running")).toBeVisible();
  expect(screen.getByRole("button", { name: "Disable schedule" })).toBeDisabled();
  vi.mocked(api.fetchBackupSchedule).mockRejectedValue(new Error("Status unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Status unavailable");
});

it("reads status without mounting unavailable schedule mutations", async () => {
  auth.edit = false;
  render(<BackupScheduleSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Schedule details" }));
  expect(await screen.findByText("Disabled")).toBeVisible();
  expect(screen.queryByLabelText("Enable scheduled backups")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save schedule" })).toBeNull();
  expect(api.fetchBackupSchedule).toHaveBeenCalledOnce();
});

it("allows explicit configuration without requesting forbidden schedule status", async () => {
  auth.view = false;
  render(<BackupScheduleSettings />);
  expect(screen.getByLabelText("Enable scheduled backups")).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Refresh status" })).toBeNull();
  expect(api.fetchBackupSchedule).not.toHaveBeenCalled();
});
it("explains that a saved settings-only schedule now makes full backups", async () => {
  vi.mocked(api.fetchBackupSchedule).mockResolvedValue({ ...empty, config, notice: "settings_schedule_now_full" });
  render(<BackupScheduleSettings />);
  fireEvent.click(screen.getByRole("button", { name: "Manage schedule" }));
  expect(await screen.findByText(/settings-only backups, which no longer exist/)).toBeVisible();
  expect(screen.queryByLabelText("Scheduled backup contents")).toBeNull();
});
