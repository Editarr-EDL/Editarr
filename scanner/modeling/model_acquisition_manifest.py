"""Pinned, verified, and recoverable acquisition of regenerable model artifacts."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from scanner.models import ModelDownloadState

_MANIFEST_RESOURCE = "model-manifest-v1.json"
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MIN_FREE_AFTER_DOWNLOAD = 64 * 1024 * 1024
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]
_SCOPED_CANCEL_CHECK: ContextVar[CancelCheck | None] = ContextVar(
    "model_acquisition_cancel_check", default=None
)


@contextmanager
def scoped_acquisition_cancellation(cancelled: CancelCheck) -> Iterator[None]:
    """Apply one caller's cancellation check to lazy acquisition in this context.

    The scan runner installs this inside its isolated child only.  Context-local
    state keeps one cancelled scan from affecting concurrent scans or API
    prefetch background threads.
    """

    token = _SCOPED_CANCEL_CHECK.set(cancelled)
    try:
        yield
    finally:
        _SCOPED_CANCEL_CHECK.reset(token)


class AcquisitionError(RuntimeError):
    """A model artifact could not be made ready."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


class AcquisitionCancelled(AcquisitionError):
    """The caller or another process cancelled acquisition."""

    def __init__(self, artifact_id: str):
        super().__init__("cancelled", f"Download cancelled for {artifact_id}.")


@dataclass(frozen=True, slots=True)
class ArtifactFile:
    path: str
    size: int
    sha256: str | None = None
    git_blob_sha1: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactLicense:
    spdx: str | None
    status: Literal["declared", "undeclared", "license-review-required"]
    url: str


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    id: str
    backend_ids: tuple[str, ...]
    backend_version: str
    distribution: Literal["huggingface", "bundled-package"]
    required_patterns: tuple[str, ...]
    source_url: str
    license: ArtifactLicense
    files: tuple[ArtifactFile, ...]
    repository: str | None = None
    revision: str | None = None
    package: str | None = None

    @property
    def expected_size(self) -> int:
        return sum(item.size for item in self.files)


@dataclass(frozen=True, slots=True)
class ModelManifest:
    schema_version: int
    manifest_version: str
    artifacts: tuple[ModelArtifact, ...]

    def artifact(self, artifact_id: str) -> ModelArtifact:
        for artifact in self.artifacts:
            if artifact.id == artifact_id:
                return artifact
        raise ValueError(f"Unknown managed model artifact: {artifact_id}")


@dataclass(frozen=True, slots=True)
class ArtifactStatus:
    artifact_id: str
    backend_ids: tuple[str, ...]
    state: Literal["not_required", "not_downloaded", "downloading", "ready", "failed", "cancelled"]
    bytes_downloaded: int | None
    bytes_total: int | None
    error_code: str | None
    error_detail: str | None
    source_url: str
    license_spdx: str | None
    license_status: Literal["declared", "undeclared", "license-review-required"]

    def download_state(self) -> ModelDownloadState:
        return ModelDownloadState(
            state=self.state,
            bytes_downloaded=self.bytes_downloaded,
            bytes_total=self.bytes_total,
            error_code=self.error_code,
            error_detail=self.error_detail,
        )


class RepositoryClient(Protocol):
    def fetch(
        self,
        artifact: ModelArtifact,
        artifact_file: ArtifactFile,
        destination: Path,
        *,
        progress: Callable[[int], None],
        cancelled: CancelCheck,
    ) -> None:
        """Stream a pinned file while reporting progress and honoring cancellation."""
        ...


class HuggingFaceRepository:
    """Small streaming client for public, immutable Hugging Face resolve URLs."""

    def fetch(
        self,
        artifact: ModelArtifact,
        artifact_file: ArtifactFile,
        destination: Path,
        *,
        progress: Callable[[int], None],
        cancelled: CancelCheck,
    ) -> None:
        """Stream a pinned file while reporting progress and honoring cancellation."""
        if artifact.repository is None or artifact.revision is None:
            raise AcquisitionError("manifest_invalid", "Downloaded artifact is not pinned.")
        quoted_path = "/".join(
            urllib.parse.quote(part, safe="") for part in PurePosixPath(artifact_file.path).parts
        )
        repo = "/".join(
            urllib.parse.quote(part, safe="") for part in artifact.repository.split("/")
        )
        url = f"https://huggingface.co/{repo}/resolve/{artifact.revision}/{quoted_path}"
        request = urllib.request.Request(url, headers={"User-Agent": "Editarr-model-acquisition/1"})
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                destination.open("wb") as out,
            ):
                while True:
                    if cancelled():
                        raise AcquisitionCancelled(artifact.id)
                    chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    progress(len(chunk))
                out.flush()
                os.fsync(out.fileno())
        except AcquisitionCancelled:
            raise
        except (OSError, urllib.error.URLError) as exc:
            raise AcquisitionError(
                "network_error",
                f"Could not download {artifact.id}. Check the network connection and retry.",
            ) from exc


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe model manifest path: {value!r}")


def _validate_required_pattern(value: str) -> None:
    """Allow globs for declared files, never paths that can describe a parent."""

    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe model manifest pattern: {value!r}")


def _validate_https_url(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Missing {label} URL")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"Invalid {label} URL")
    return value


def load_manifest() -> ModelManifest:
    """Load and validate the bundled manifest without network or cache writes."""

    resource = files("scanner").joinpath(_MANIFEST_RESOURCE)
    raw = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Invalid model manifest root")
    if raw.get("schema_version") != 1:
        raise ValueError("Unsupported model manifest schema")
    manifest_version = raw.get("manifest_version")
    if not isinstance(manifest_version, str) or not manifest_version.strip():
        raise ValueError("Missing model manifest version")
    raw_artifacts = raw.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ValueError("Model manifest contains no artifacts")
    artifacts: list[ModelArtifact] = []
    seen: set[str] = set()
    for item in raw_artifacts:
        if not isinstance(item, dict):
            raise ValueError("Invalid model manifest artifact entry")
        artifact_id = item.get("id")
        if not isinstance(artifact_id, str):
            raise ValueError("Invalid model artifact id")
        if not _SAFE_ID.fullmatch(artifact_id) or artifact_id in seen:
            raise ValueError(f"Invalid or duplicate model artifact id: {artifact_id!r}")
        seen.add(artifact_id)
        raw_backend_ids = item.get("backend_ids", [])
        if not isinstance(raw_backend_ids, list) or not all(
            isinstance(value, str) and value.strip() for value in raw_backend_ids
        ):
            raise ValueError(f"Artifact {artifact_id} has invalid backend ownership")
        backend_ids = tuple(raw_backend_ids)
        if len(backend_ids) != len(set(backend_ids)):
            raise ValueError(f"Artifact {artifact_id} has duplicate backend ownership")
        raw_files = item.get("files", [])
        if not isinstance(raw_files, list):
            raise ValueError(f"Artifact {artifact_id} has invalid files")
        artifact_files: list[ArtifactFile] = []
        for entry in raw_files:
            if not isinstance(entry, dict):
                raise ValueError(f"Artifact {artifact_id} has invalid file metadata")
            path = entry.get("path")
            size = entry.get("size")
            sha256 = entry.get("sha256")
            git_blob_sha1 = entry.get("git_blob_sha1")
            if not isinstance(path, str) or not isinstance(size, int) or isinstance(size, bool):
                raise ValueError(f"Artifact {artifact_id} has invalid file metadata")
            if sha256 is not None and not isinstance(sha256, str):
                raise ValueError(f"Artifact {artifact_id} has invalid SHA-256 metadata")
            if git_blob_sha1 is not None and not isinstance(git_blob_sha1, str):
                raise ValueError(f"Artifact {artifact_id} has invalid Git SHA-1 metadata")
            artifact_files.append(
                ArtifactFile(
                    path=path,
                    size=size,
                    sha256=sha256,
                    git_blob_sha1=git_blob_sha1,
                )
            )
        artifact_files_tuple = tuple(artifact_files)
        for artifact_file in artifact_files_tuple:
            _validate_relative_path(artifact_file.path)
            if artifact_file.size < 0 or not (artifact_file.sha256 or artifact_file.git_blob_sha1):
                raise ValueError(
                    f"Incomplete integrity metadata for {artifact_id}/{artifact_file.path}"
                )
            if artifact_file.sha256 and not re.fullmatch(r"[0-9a-f]{64}", artifact_file.sha256):
                raise ValueError(f"Invalid SHA-256 for {artifact_id}/{artifact_file.path}")
            if artifact_file.git_blob_sha1 and not re.fullmatch(
                r"[0-9a-f]{40}", artifact_file.git_blob_sha1
            ):
                raise ValueError(f"Invalid Git blob SHA-1 for {artifact_id}/{artifact_file.path}")
        file_paths = [artifact_file.path for artifact_file in artifact_files_tuple]
        if len(file_paths) != len(set(file_paths)):
            raise ValueError(f"Duplicate file declaration for {artifact_id}")
        distribution = item.get("distribution")
        if distribution not in {"huggingface", "bundled-package"}:
            raise ValueError(f"Unsupported distribution for {artifact_id}")
        if distribution == "huggingface" and not backend_ids:
            raise ValueError(f"Downloaded artifact {artifact_id} has no backend ownership")
        raw_patterns = item.get("required_patterns", [])
        if (
            not isinstance(raw_patterns, list)
            or not raw_patterns
            or not all(isinstance(value, str) and value for value in raw_patterns)
        ):
            raise ValueError(f"Artifact {artifact_id} has no required file patterns")
        required_patterns = tuple(raw_patterns)
        for pattern in required_patterns:
            _validate_required_pattern(pattern)
        if distribution == "huggingface" and any(
            not any(fnmatch.fnmatch(path, pattern) for path in file_paths)
            for pattern in required_patterns
        ):
            raise ValueError(f"Required pattern has no pinned file for {artifact_id}")
        repository = item.get("repository")
        revision = item.get("revision")
        if distribution == "huggingface" and (
            not isinstance(repository, str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*",
                repository,
            )
            or not isinstance(revision, str)
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
        ):
            raise ValueError(f"Downloaded artifact {artifact_id} is not immutably pinned")
        if distribution == "bundled-package" and not isinstance(item.get("package"), str):
            raise ValueError(f"Package-supplied artifact {artifact_id} has no package")
        license_data = item.get("license")
        if not isinstance(license_data, dict):
            raise ValueError(f"Missing license metadata for {artifact_id}")
        if license_data.get("status") not in {"declared", "undeclared", "license-review-required"}:
            raise ValueError(f"Invalid license status for {artifact_id}")
        spdx = license_data.get("spdx")
        if spdx is not None and (not isinstance(spdx, str) or not spdx.strip()):
            raise ValueError(f"Invalid SPDX metadata for {artifact_id}")
        if license_data["status"] == "declared" and spdx is None:
            raise ValueError(f"Declared license has no SPDX expression for {artifact_id}")
        source_url = _validate_https_url(item.get("source_url"), "source")
        license_url = _validate_https_url(license_data.get("url"), "license")
        backend_version = item.get("backend_version")
        if not isinstance(backend_version, str) or not backend_version.strip():
            raise ValueError(f"Artifact {artifact_id} has no backend version")
        artifacts.append(
            ModelArtifact(
                id=artifact_id,
                backend_ids=backend_ids,
                backend_version=backend_version,
                distribution=distribution,
                required_patterns=required_patterns,
                source_url=source_url,
                license=ArtifactLicense(
                    spdx=spdx,
                    status=license_data["status"],
                    url=license_url,
                ),
                files=artifact_files_tuple,
                repository=repository,
                revision=revision,
                package=item.get("package"),
            )
        )
    return ModelManifest(
        schema_version=1,
        manifest_version=manifest_version,
        artifacts=tuple(artifacts),
    )


_MANIFEST = load_manifest()


def manifest() -> ModelManifest:
    return _MANIFEST


def artifact_ids_for_backend(
    backend_id: str, *, wd_variant: str = "eva02-large"
) -> tuple[str, ...]:
    """Return the exact downloadable artifact set for one public backend."""

    mapping = {
        "whisper": ("faster-whisper-base",),
        "mlx_whisper": ("mlx-whisper-base",),
        "apple_speech": (),
        "image_safety_gate": (
            f"wd-{wd_variant}-tagger-v3",
            "stable-diffusion-safety-checker",
            "image-safety-classifier-s",
        ),
        "siglip2_moderation_nsfw": (
            "siglip2-base-patch16-256",
            "siglip2-moderation-head-nsfw",
        ),
        "siglip2_moderation_violence": (
            "siglip2-base-patch16-256",
            "siglip2-moderation-head-violence",
        ),
        "shieldstral": ("shieldstral-3b-int8",),
        "qwen3-vl-8b": ("qwen3-vl-8b-instruct-4bit",),
        "qwen3-vl-4b": ("qwen3-vl-4b-instruct-4bit",),
        "qwen38-27b": ("qwen38-27b-instruct-4bit",),
        "d1-3b": ("d1-3b",),
        "qwen3_vl": ("qwen3-vl-8b-instruct-4bit",),
        "qwen": ("qwen3-vl-8b-instruct-4bit",),
        "nudenet": (),
        "better_profanity": (),
        "glin_profanity": (),
        "alt_profanity_check": (),
        "alt-profanity-check": (),
    }
    try:
        artifact_ids = mapping[backend_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported model backend: {backend_id}") from exc
    for artifact_id in artifact_ids:
        _MANIFEST.artifact(artifact_id)
    return artifact_ids
