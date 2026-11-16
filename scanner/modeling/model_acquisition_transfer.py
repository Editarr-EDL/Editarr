"""Download, verify, and publish one managed artifact."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from scanner.runtime.runtime_paths import ensure_user_private_directory

from .model_acquisition_authority import publish_acquisition, require_acquisition
from .model_acquisition_manifest import (
    _SCOPED_CANCEL_CHECK,
    AcquisitionCancelled,
    AcquisitionError,
    CancelCheck,
    ProgressCallback,
)
from .model_acquisition_storage import _atomic_json, _cross_process_lock

if TYPE_CHECKING:
    from .model_acquisition_service import ModelAcquisitionService


def acquire(
    service: ModelAcquisitionService,
    artifact_id: str,
    *,
    progress: ProgressCallback | None = None,
    cancelled: CancelCheck | None = None,
    offline: bool = False,
) -> Path:
    """Acquire one pinned artifact and recheck authority before fetching or publishing."""
    require_acquisition()
    if cancelled is None:
        cancelled = _SCOPED_CANCEL_CHECK.get()
    artifact = service.manifest.artifact(artifact_id)
    if artifact.distribution != "huggingface":
        raise AcquisitionError("not_downloadable", f"{artifact_id} is supplied by the application.")
    ensure_user_private_directory(service.cache_root)
    service._validate_cache_layout()
    cancel_path = service._validated_control_path(service._cancel_path(artifact.id), ".cancel")
    lock_path = service._validated_control_path(service._lock_path(artifact.id), ".locks")
    previous = service._read_state(artifact.id) or {}
    if previous.get("state") != "downloading":
        cancel_path.unlink(missing_ok=True)

    def cancel_check() -> bool:
        require_acquisition()
        return service._is_cancelled(artifact.id, cancelled)

    with _cross_process_lock(lock_path, cancel_check):
        if service._is_ready(artifact, verify_content=True):
            return service._artifact_dir(artifact)
        if offline:
            raise AcquisitionError(
                "offline_missing",
                f"{artifact.id} is not installed. Connect to the network and download it first.",
            )
        require_acquisition(download=True)
        service._cancel_path(artifact.id).unlink(missing_ok=True)
        staging_root = service.cache_root / ".staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        for stale in staging_root.glob(f"{artifact.id}.partial-*"):
            if stale.is_symlink():
                stale.unlink()
            elif stale.is_dir():
                shutil.rmtree(stale)
        staging = staging_root / f"{artifact.id}.partial-{os.getpid()}-{uuid.uuid4().hex}"
        staging.mkdir(mode=0o700)
        downloaded = 0
        replaced = staging_root / f"{artifact.id}.replaced-{uuid.uuid4().hex}"

        def download_cancelled() -> bool:
            require_acquisition(download=True)
            return service._is_cancelled(artifact.id, cancelled)

        def advance(count: int) -> None:
            nonlocal downloaded
            require_acquisition(download=True)
            downloaded += count
            service._write_state(artifact, "downloading", bytes_downloaded=downloaded)
            if progress:
                progress(downloaded, artifact.expected_size)

        def publish() -> Path:
            final = service._artifact_dir(artifact)
            final.parent.mkdir(parents=True, exist_ok=True)
            if (
                final.is_symlink()
                or final.parent.is_symlink()
                or not final.resolve().is_relative_to(service.artifact_root.resolve())
            ):
                raise AcquisitionError(
                    "unsafe_path", "Refused an unsafe managed artifact destination."
                )
            if final.exists():
                os.replace(final, replaced)
            os.replace(staging, final)
            service._write_state(artifact, "ready", bytes_downloaded=artifact.expected_size)
            return final

        try:
            required = artifact.expected_size + service.minimum_free_after_download
            if service.disk_free(service.cache_root) < required:
                raise AcquisitionError(
                    "insufficient_space",
                    f"Not enough disk space for {artifact.id}; free at least {required} bytes.",
                )
            service._write_state(artifact, "downloading", bytes_downloaded=0)
            for item in artifact.files:
                if download_cancelled():
                    raise AcquisitionCancelled(artifact.id)
                destination = staging.joinpath(*PurePosixPath(item.path).parts)
                service.repository.fetch(
                    artifact, item, destination, progress=advance, cancelled=download_cancelled
                )
            if download_cancelled():
                raise AcquisitionCancelled(artifact.id)
            verified = service._verified_files(artifact, staging)
            _atomic_json(
                staging / ".complete.json",
                {
                    "schema_version": 1,
                    "manifest_version": service.manifest.manifest_version,
                    "artifact_id": artifact.id,
                    "revision": artifact.revision,
                    "files": verified,
                },
            )
            return publish_acquisition(publish)
        except AcquisitionCancelled:
            service._write_state(artifact, "cancelled", bytes_downloaded=downloaded)
            raise
        except AcquisitionError as exc:
            service._write_state(
                artifact,
                "failed",
                bytes_downloaded=downloaded,
                error_code=exc.code,
                error_detail=exc.detail,
            )
            raise
        except Exception as exc:
            from scanner.security.scan_jobs.authority import JobRefusedError

            if isinstance(exc, JobRefusedError):
                service._write_state(artifact, "cancelled", bytes_downloaded=downloaded)
                raise
            error = AcquisitionError(
                "download_failed", f"Could not install {artifact.id}. Check the network and retry."
            )
            service._write_state(
                artifact,
                "failed",
                bytes_downloaded=downloaded,
                error_code=error.code,
                error_detail=error.detail,
            )
            raise error from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if replaced.exists():
                shutil.rmtree(replaced)
