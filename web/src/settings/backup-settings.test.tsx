import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackupSettings } from "./backup-settings";
import * as backups from "../api/api-backups";

vi.mock("../auth/auth-gate", async (importOriginal) => ({
  ...await importOriginal<typeof import("../auth/auth-gate")>(),
  useCan: () => true,
}));
vi.mock("./backup-schedule-settings", () => ({ BackupScheduleSettings: () => null }));

vi.mock("../api/api-backups", () => ({
  startBackupExport: vi.fn(), backupStatus: vi.fn(), cancelBackup: vi.fn(),
  checkBackupDownload: vi.fn(),
  backupDownloadUrl: (id: string) => `/api/backups/jobs/${id}/download`,
}));

const job = (state: string) => ({
  id: "test-job", state, action: "export", kind: "full", plan: null, error: null, reason: null,
  inventory: null, report: null,
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("backup settings", () => {
  it("creates a plain full backup without requesting a passphrase", async () => {
    vi.mocked(backups.startBackupExport).mockResolvedValue(job("ready") as never);
    render(<BackupSettings />);
    expect(screen.getByText(/Unencrypted: included credentials/)).toBeVisible();
    expect(screen.queryByLabelText("Passphrase")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    await waitFor(() => expect(backups.startBackupExport).toHaveBeenCalledWith({}));
    expect(screen.getByRole("button", { name: "Download backup" })).toBeVisible();
  });

  it("offers no settings-only backup and no restore or inspection controls", () => {
    render(<BackupSettings />);
    expect(screen.queryByLabelText("Backup contents")).toBeNull();
    expect(screen.queryByLabelText("Backup file")).toBeNull();
    expect(screen.queryByRole("button", { name: "Inspect backup" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore backup" })).toBeNull();
    expect(screen.getByText(/first-run setup screen of a new, empty installation/)).toBeVisible();
  });

  it("only requests a passphrase when encryption is selected", async () => {
    vi.mocked(backups.startBackupExport).mockResolvedValue(job("ready") as never);
    render(<BackupSettings />);
    fireEvent.click(screen.getByLabelText("Encrypt backup"));
    expect(screen.getByLabelText("Passphrase")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter matching backup passphrases");
    fireEvent.change(screen.getByLabelText("Passphrase"), { target: { value: "local-secret" } });
    fireEvent.change(screen.getByLabelText("Confirm passphrase"), { target: { value: "local-secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    await waitFor(() => expect(backups.startBackupExport).toHaveBeenCalledWith({
      encrypt: true, passphrase: "local-secret",
    }));
    expect(screen.queryByDisplayValue("local-secret")).toBeNull();
  });

  it("reports an unavailable download without claiming that it saved a file", async () => {
    vi.mocked(backups.startBackupExport).mockResolvedValue(job("ready") as never);
    vi.mocked(backups.checkBackupDownload).mockRejectedValue(new Error("Backup download expired"));
    render(<BackupSettings />);
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    fireEvent.click(await screen.findByRole("button", { name: "Download backup" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Backup download expired");
  });

  it("cancels a finished job", async () => {
    vi.mocked(backups.startBackupExport).mockResolvedValue(job("ready") as never);
    vi.mocked(backups.cancelBackup).mockResolvedValue(job("cancelled") as never);
    render(<BackupSettings />);
    fireEvent.click(screen.getByRole("button", { name: "Create backup" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel job" }));
    expect(await screen.findByText("Backup cancelled.")).toBeVisible();
    expect(backups.cancelBackup).toHaveBeenCalledWith("test-job");
  });
});
