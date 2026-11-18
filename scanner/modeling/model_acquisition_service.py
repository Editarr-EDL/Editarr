"""Verified, cancellable acquisition of managed model artifacts."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from scanner.models import ModelDownloadState
from scanner.runtime.distribution import is_macos_release
from scanner.runtime.runtime_paths import ensure_user_private_directory

from .model_acquisition_authority import (
    DOWNLOADS_REFUSED_DETAIL as DOWNLOADS_REFUSED_DETAIL,
)
from .model_acquisition_authority import (
    _downloads_refused as _downloads_refused,
)
from .model_acquisition_authority import (
    refuse_model_downloads as refuse_model_downloads,
)
from .model_acquisition_manifest import (
    _MANIFEST,
    _MIN_FREE_AFTER_DOWNLOAD,
    AcquisitionError,
    ArtifactStatus,
    CancelCheck,
    HuggingFaceRepository,
    ModelArtifact,
    ModelManifest,
    ProgressCallback,
    RepositoryClient,
    artifact_ids_for_backend,
)
from .model_acquisition_storage import (
    _LOCAL_LOCKS,
    _LOCAL_LOCKS_GUARD,
    _atomic_json,
    _cross_process_lock,
    _file_integrity,
)


class ModelAcquisitionService:
    """Manage one manifest's artifacts beneath an Editarr-owned cache root."""

    def __init__(
        self,
        cache_root: Path,
        *,
        repository: RepositoryClient | None = None,
        disk_free: Callable[[Path], int] | None = None,
        model_manifest: ModelManifest | None = None,
        minimum_free_after_download: int = _MIN_FREE_AFTER_DOWNLOAD,
    ):
        """Configure cache storage, repository transport, free-space checks, and manifest."""
        self.cache_root = Path(cache_root)
        self.repository = repository or HuggingFaceRepository()
        self.disk_free = disk_free or (lambda path: shutil.disk_usage(path).free)
        self.manifest = model_manifest or _MANIFEST
        self.minimum_free_after_download = minimum_free_after_download

    @property
    def artifact_root(self) -> Path:
        return self.cache_root / "artifacts"

    def _artifact_dir(self, artifact: ModelArtifact) -> Path:
        assert artifact.revision is not None
        return self.artifact_root / artifact.id / artifact.revision

    def _validate_cache_layout(self) -> None:
        """Reject internal cache roots redirected beyond the configured cache."""

        root = self.cache_root.resolve()
        for name in ("artifacts", ".state", ".cancel", ".locks", ".staging"):
            if not (self.cache_root / name).resolve().is_relative_to(root):
                raise AcquisitionError(
                    "unsafe_path", "Refused an unsafe managed model-cache layout."
                )

    def _state_path(self, artifact_id: str) -> Path:
        return self.cache_root / ".state" / f"{artifact_id}.json"

    def _cancel_path(self, artifact_id: str) -> Path:
        return self.cache_root / ".cancel" / artifact_id

    def _lock_path(self, artifact_id: str) -> Path:
        return self.cache_root / ".locks" / f"{artifact_id}.lock"

    def _validated_control_path(self, path: Path, parent_name: str) -> Path:
        parent = self.cache_root / parent_name
        if path.is_symlink() or not path.resolve().is_relative_to(parent.resolve()):
            raise AcquisitionError("unsafe_path", "Refused an unsafe model-cache control path.")
        return path

    def _is_cancelled(self, artifact_id: str, caller: CancelCheck | None) -> bool:
        return bool((caller and caller()) or self._cancel_path(artifact_id).is_file())

    def _read_state(self, artifact_id: str) -> dict[str, Any] | None:
        """Read state for an artifact only when its identity matches the active manifest."""
        artifact = self.manifest.artifact(artifact_id)
        state_path = self._state_path(artifact_id)
        if state_path.is_symlink():
            return None
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            if (
                value.get("artifact_id") != artifact.id
                or value.get("manifest_version") != self.manifest.manifest_version
                or value.get("revision") != artifact.revision
            ):
                return None
            return value
        except (OSError, json.JSONDecodeError):
            return None

    def _write_state(self, artifact: ModelArtifact, state: str, **values: Any) -> None:
        """Atomically record an artifact state and additional progress fields."""
        _atomic_json(
            self._state_path(artifact.id),
            {
                "artifact_id": artifact.id,
                "manifest_version": self.manifest.manifest_version,
                "revision": artifact.revision,
                "state": state,
                "bytes_total": artifact.expected_size,
                **values,
            },
        )

    def _acquisition_active(self, artifact_id: str) -> bool:
        """Probe an existing lock without creating cache state or waiting for it."""

        lock_path = self._lock_path(artifact_id)
        if not lock_path.is_file() or lock_path.is_symlink():
            return False
        key = str(lock_path.absolute())
        with _LOCAL_LOCKS_GUARD:
            local = _LOCAL_LOCKS.get(key)
        if local is not None and not local.acquire(blocking=False):
            return True
        try:
            with lock_path.open("r+b") as handle:
                if os.name == "nt":
                    import msvcrt

                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    except OSError:
                        return True
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return True
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
        except OSError:
            # A concurrently replaced or inaccessible lock cannot safely be called stale.
            return True
        finally:
            if local is not None:
                local.release()

    def _verified_files(self, artifact: ModelArtifact, directory: Path) -> list[dict[str, Any]]:
        """Hash every required file beneath directory and return completion metadata."""
        verified: list[dict[str, Any]] = []
        for item in artifact.files:
            target = directory.joinpath(*PurePosixPath(item.path).parts)
            if not target.is_file():
                raise AcquisitionError(
                    "missing_file", f"Required file is missing for {artifact.id}."
                )
            size, sha256, git_blob_sha1 = _file_integrity(target)
            if (
                size != item.size
                or (item.sha256 and sha256 != item.sha256)
                or (item.git_blob_sha1 and git_blob_sha1 != item.git_blob_sha1)
            ):
                raise AcquisitionError(
                    "integrity_mismatch",
                    f"Downloaded files for {artifact.id} failed integrity verification; retry.",
                )
            verified.append(
                {
                    "path": item.path,
                    "size": size,
                    "mtime_ns": target.stat().st_mtime_ns,
                    "sha256": sha256,
                }
            )
        return verified

    def _is_ready(self, artifact: ModelArtifact, *, verify_content: bool = False) -> bool:
        """Check completion metadata and optionally rehash each artifact file."""
        if artifact.distribution == "bundled-package":
            return True
        try:
            self._validate_cache_layout()
            artifact_root = self.artifact_root.resolve()
            if not self._artifact_dir(artifact).resolve().is_relative_to(artifact_root):
                return False
        except AcquisitionError:
            return False
        directory = self._artifact_dir(artifact)
        marker = directory / ".complete.json"
        try:
            if directory.is_symlink() or marker.is_symlink():
                return False
            completed = json.loads(marker.read_text(encoding="utf-8"))
            if (
                completed.get("manifest_version") != self.manifest.manifest_version
                or completed.get("artifact_id") != artifact.id
                or completed.get("revision") != artifact.revision
            ):
                return False
            completed_files = {item.get("path"): item for item in completed.get("files", [])}
            for item in artifact.files:
                target = directory.joinpath(*PurePosixPath(item.path).parts)
                if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
                    return False
                recorded = completed_files.get(item.path) or {}
                current = target.stat()
                if (
                    not target.is_file()
                    or current.st_size != item.size
                    or recorded.get("size") != item.size
                    or recorded.get("mtime_ns") != current.st_mtime_ns
                ):
                    return False
            if verify_content:
                self._verified_files(artifact, directory)
            return True
        except (OSError, json.JSONDecodeError, AcquisitionError, TypeError):
            return False

    def status(self, artifact_id: str) -> ArtifactStatus:
        """Report the current safe acquisition state for one manifest artifact."""
        artifact = self.manifest.artifact(artifact_id)
        if artifact.distribution == "bundled-package":
            state = "not_required"
            values: dict[str, Any] = {}
        elif self._cache_layout_is_unsafe():
            state = "failed"
            values = {
                "error_code": "unsafe_path",
                "error_detail": "The managed model-cache layout is unsafe.",
            }
        elif self._is_ready(artifact):
            state = "ready"
            values = {
                "bytes_downloaded": artifact.expected_size,
                "bytes_total": artifact.expected_size,
            }
        else:
            current = self._read_state(artifact.id) or {}
            state = current.get("state", "not_downloaded")
            if state not in {"not_downloaded", "downloading", "failed", "cancelled"}:
                state = "not_downloaded"
            values = current
            if state == "downloading" and not self._acquisition_active(artifact.id):
                state = "failed"
                values = {
                    **current,
                    "error_code": "interrupted",
                    "error_detail": f"Download was interrupted for {artifact.id}; retry.",
                }
        return ArtifactStatus(
            artifact_id=artifact.id,
            backend_ids=artifact.backend_ids,
            state=state,
            bytes_downloaded=values.get("bytes_downloaded"),
            bytes_total=values.get("bytes_total", artifact.expected_size or None),
            error_code=values.get("error_code"),
            error_detail=values.get("error_detail"),
            source_url=artifact.source_url,
            license_spdx=artifact.license.spdx,
            license_status=artifact.license.status,
        )

    def _cache_layout_is_unsafe(self) -> bool:
        try:
            self._validate_cache_layout()
            return False
        except AcquisitionError:
            return True

    def list_artifacts(self) -> tuple[ArtifactStatus, ...]:
        # The macOS release ships its own FFmpeg helpers in place of this package binary.
        replaced_binary = "ffmpeg-package-binary" if is_macos_release() else None
        return tuple(
            self.status(artifact.id)
            for artifact in self.manifest.artifacts
            if artifact.id != replaced_binary
        )

    def backend_status(
        self, backend_id: str, *, wd_variant: str = "eva02-large"
    ) -> ModelDownloadState:
        """Combine all artifacts required by a backend into one download state."""
        artifact_ids = artifact_ids_for_backend(backend_id, wd_variant=wd_variant)
        if not artifact_ids:
            return ModelDownloadState(state="not_required")
        statuses = [self.status(artifact_id) for artifact_id in artifact_ids]
        total = sum(item.bytes_total or 0 for item in statuses)
        downloaded = sum(item.bytes_downloaded or 0 for item in statuses)
        priority = ("failed", "cancelled", "downloading", "not_downloaded", "ready")
        state = next(value for value in priority if any(item.state == value for item in statuses))
        problem = next((item for item in statuses if item.state in {"failed", "cancelled"}), None)
        return ModelDownloadState(
            state=state,
            bytes_downloaded=downloaded,
            bytes_total=total,
            error_code=problem.error_code if problem else None,
            error_detail=problem.error_detail if problem else None,
        )

    def request_cancel(self, artifact_id: str) -> None:
        self.manifest.artifact(artifact_id)
        ensure_user_private_directory(self.cache_root)
        self._validate_cache_layout()
        path = self._validated_control_path(self._cancel_path(artifact_id), ".cancel")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def acquire(
        self,
        artifact_id: str,
        *,
        progress: ProgressCallback | None = None,
        cancelled: CancelCheck | None = None,
        offline: bool = False,
    ) -> Path:
        """Acquire one pinned artifact, honoring progress, cancellation, and offline mode."""
        from .model_acquisition_transfer import acquire

        return acquire(self, artifact_id, progress=progress, cancelled=cancelled, offline=offline)

    def prefetch_backend(
        self,
        backend_id: str,
        *,
        wd_variant: str = "eva02-large",
        progress: Callable[[str, int, int], None] | None = None,
        cancelled: CancelCheck | None = None,
        offline: bool = False,
    ) -> dict[str, Path]:
        """Acquire every artifact for a backend and return their local paths."""
        artifact_ids = artifact_ids_for_backend(backend_id, wd_variant=wd_variant)
        paths: dict[str, Path] = {}
        for artifact_id in artifact_ids:
            paths[artifact_id] = self.acquire(
                artifact_id,
                progress=(
                    (lambda done, total, item=artifact_id: progress(item, done, total))
                    if progress
                    else None
                ),
                cancelled=cancelled,
                offline=offline,
            )
        return paths

    def remove(self, artifact_id: str) -> bool:
        """Remove one regenerable artifact after validating cache paths and obtaining its lock."""
        artifact = self.manifest.artifact(artifact_id)
        if artifact.distribution != "huggingface":
            raise AcquisitionError(
                "not_removable", f"{artifact_id} is supplied by the application."
            )
        ensure_user_private_directory(self.cache_root)
        self._validate_cache_layout()
        lock_path = self._validated_control_path(self._lock_path(artifact.id), ".locks")
        with _cross_process_lock(lock_path, lambda: False):
            target = self.artifact_root / artifact.id
            cache_root = self.cache_root.resolve()
            artifact_root = self.artifact_root.resolve()
            if not artifact_root.is_relative_to(cache_root):
                raise AcquisitionError("unsafe_path", "Refused an unsafe model-cache removal.")
            existed = target.exists() or target.is_symlink()
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                if not target.resolve().is_relative_to(artifact_root):
                    raise AcquisitionError("unsafe_path", "Refused an unsafe model-cache removal.")
                shutil.rmtree(target)
            elif target.exists():
                raise AcquisitionError("unsafe_path", "Refused an unsafe model-cache removal.")
            self._state_path(artifact.id).unlink(missing_ok=True)
            self._cancel_path(artifact.id).unlink(missing_ok=True)
            return existed


def get_acquisition_service(cache_root: Path | None = None) -> ModelAcquisitionService:
    if cache_root is None:
        from scanner.settings.config import settings

        cache_root = settings.model_cache
    return ModelAcquisitionService(Path(cache_root))


def acquire_backend(
    backend_id: str,
    *,
    wd_variant: str = "eva02-large",
    offline: bool = False,
) -> dict[str, Path]:
    """Acquire the artifacts required by a backend through the configured service."""

    return get_acquisition_service().prefetch_backend(
        backend_id,
        wd_variant=wd_variant,
        offline=offline,
    )


def acquire_artifact(artifact_id: str, *, offline: bool = False) -> Path:
    """Acquire one selected artifact without prefetching its backend siblings."""

    return get_acquisition_service().acquire(artifact_id, offline=offline)
