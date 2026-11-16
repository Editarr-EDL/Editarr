"""Private, machine-local schedule state and unattended credential validation."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scanner.runtime.runtime_paths import ensure_user_private_directory

from .archive import BackupError
from .snapshots import _sync_directory

STATE_FILE = "backup-schedule.json"
SETTINGS_SCHEDULE_NOW_FULL = "settings_schedule_now_full"


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    interval_hours: int = Field(ge=1, le=8760)
    keep_count: int = Field(ge=1, le=1000)
    destination: str = Field(min_length=1, max_length=4096)
    kind: Literal["full"] = "full"
    protection: Literal["plain", "age-scrypt"] = "plain"
    passphrase_file: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_paths(self):
        if not Path(self.destination).is_absolute():
            raise ValueError("An absolute backup destination is required")
        if self.protection == "age-scrypt":
            if not self.passphrase_file or not Path(self.passphrase_file).is_absolute():
                raise ValueError("An absolute passphrase file is required")
        elif self.passphrase_file is not None:
            raise ValueError("Plain backups do not use a passphrase file")
        return self


class ScheduledArchive(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[a-f0-9]{32}\.(backup|age)$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)
    created_at: float = Field(ge=0, allow_inf_nan=False)


class ScheduleState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    config: ScheduleConfig | None = None
    owner: str = Field(default="", pattern=r"^([a-f0-9]{32})?$")
    destination_device: int | None = None
    destination_inode: int | None = None
    output_device: int | None = None
    output_inode: int | None = None
    next_run: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    last_attempt: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    last_success: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    outcome: Literal["idle", "running", "succeeded", "failed", "interrupted"] = "idle"
    error: str | None = None
    archives: list[ScheduledArchive] = Field(default_factory=list, max_length=1001)
    notice: Literal["settings_schedule_now_full"] | None = None

    @model_validator(mode="before")
    @classmethod
    def settings_backups_become_full(cls, data):
        """Load a saved settings-only schedule as a full one, with a notice until saved again."""
        config = data.get("config") if isinstance(data, dict) else None
        if isinstance(config, dict) and config.get("kind") == "settings":
            data = {**data, "config": {**config, "kind": "full"}}
            data["notice"] = SETTINGS_SCHEDULE_NOW_FULL
        return data


def read_state(data_dir: Path) -> ScheduleState:
    path = data_dir / STATE_FILE
    try:
        if path.is_symlink():
            raise BackupError("Scheduled backup settings are unsafe")
        with path.open("rb") as stream:
            content = stream.read(512 * 1024 + 1)
        if len(content) > 512 * 1024:
            raise ValueError("State too large")
        return ScheduleState.model_validate_json(content)
    except FileNotFoundError:
        return ScheduleState()
    except (ValueError, OSError) as error:
        raise BackupError("Scheduled backup settings are unavailable") from error


def write_state(data_dir: Path, state: ScheduleState) -> None:
    directory = ensure_user_private_directory(data_dir)
    descriptor, name = tempfile.mkstemp(prefix=".backup-schedule-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state.model_dump(), stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / STATE_FILE)
        _sync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)


def destination_identity(
    config: ScheduleConfig, data_dir: Path, served_roots: tuple[Path, ...] = ()
) -> tuple[int, int]:
    """Check an existing destination without creating it or changing its permissions.

    `served_roots` are directories that HTTP serves as static files; an archive
    written beneath one could be downloaded without authentication.
    """
    destination = Path(config.destination)
    if destination.is_symlink() or not destination.is_dir():
        raise BackupError("Backup destination must be an existing regular directory")
    if destination.resolve().is_relative_to(data_dir.resolve()):
        raise BackupError("Scheduled backups must be outside the application data directory")
    if any(destination.resolve().is_relative_to(root.resolve()) for root in served_roots):
        raise BackupError("Scheduled backups must be outside served web files")
    descriptor, name = tempfile.mkstemp(prefix=".editarr-write-check-", dir=destination)
    os.close(descriptor)
    Path(name).unlink()
    info = destination.stat()
    return info.st_dev, info.st_ino


def read_passphrase(config: ScheduleConfig, data_dir: Path) -> str | None:
    """Read only an owner-private external regular file; never persist its contents."""
    if config.protection == "plain":
        return None
    if os.name != "posix":
        raise BackupError("Protected passphrase files require POSIX permission validation")
    path = Path(config.passphrase_file or "")
    resolved = path.resolve()
    if any(
        resolved.is_relative_to(root.resolve())
        for root in (
            data_dir,
            Path(config.destination),
        )
    ):
        raise BackupError("Passphrase file must be outside application data and backup destination")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            raise BackupError("Passphrase file must be an owner-only regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(4097)
        if len(content) > 4096:
            raise BackupError("Invalid passphrase file")
        passphrase = content.decode("utf-8").removesuffix("\n").removesuffix("\r")
        if not passphrase or any(character in passphrase for character in "\r\n\0"):
            raise BackupError("Invalid passphrase file")
        return passphrase
    finally:
        os.close(descriptor)
