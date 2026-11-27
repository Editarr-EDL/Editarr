"""EDL and MCF import routes."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.api import _import_error_responses
from scanner.formats.edl import EdlParseError
from scanner.mcf import (
    MAX_CONTENT_BYTES as MAX_MCF_CONTENT_BYTES,
)
from scanner.mcf import (
    McfParseError,
    parse_mcf_content_with_recovery,
)
from scanner.models import (
    EdlImportOptions,
    EdlImportResult,
    McfImportIssue,
    McfImportOptions,
    McfImportResult,
)
from scanner.persistence.repository.review_access import CONTENT_EDITS, ReviewAccess
from scanner.repository import (
    CreditsAcceptanceError,
    McfImportTimingError,
    MediaRuntimeUnavailable,
    import_edl_as_saved_review,
    import_mcf_as_saved_review,
    resolve_local_path,
)
from scanner.runtime.media_fingerprints import SampledMediaFingerprint, sampled_media_fingerprint
from scanner.runtime.scan_version import ScanVersionChanged, file_generation
from scanner.security.access.capability_ids import Capability as C

from .drained_threads import drained_thread
from .review_access import review_caller
from .review_import_helpers_api import (
    _import_error_line,
    _import_target_media,
    _ImportMediaUnavailable,
    _McfImportIssues,
    _refuse_another_source,
    _safe_import_basename,
    _safe_mcf_import_basename,
)
from .review_targets import capture_target
from .stable_media_capture import cached_target_digest, observe_sampled_target_version

router = APIRouter()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StagedVersion:
    fingerprint: SampledMediaFingerprint
    known_content_sha256: str | None


def _target_generation(path: str) -> tuple[int, ...]:
    try:
        return file_generation(path)
    except ScanVersionChanged as exc:
        raise root._review_http_error(
            "target_changed", "The selected media file changed during import", 409
        ) from exc


def _capture_staged_version(caller, prepared, *, capabilities=CONTENT_EDITS, scan_ids=()):
    """Authorize all import capabilities before reading the selected media file."""
    caller.authorize_create(
        prepared.media,
        capability=C.REVIEWS_IMPORT_UPLOAD,
        capabilities=capabilities,
        scan_ids=scan_ids,
    )
    path = prepared.media.resolved_path
    generation = _target_generation(path)
    try:
        fingerprint = sampled_media_fingerprint(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise root._review_http_error(
            "target_changed", "The selected media file changed during import", 409
        ) from exc
    if fingerprint.generation != generation:
        raise root._review_http_error(
            "target_changed", "The selected media file changed during import", 409
        )
    return _StagedVersion(fingerprint, cached_target_digest(caller.db, prepared))


def _store_staged_version(db, prepared, staged_version: _StagedVersion | None):
    if staged_version is None:
        return None
    if _target_generation(prepared.media.resolved_path) != staged_version.fingerprint.generation:
        raise root._review_http_error(
            "target_changed", "The selected media file changed during import", 409
        )
    try:
        return observe_sampled_target_version(
            db,
            prepared,
            fingerprint=staged_version.fingerprint,
            known_content_sha256=staged_version.known_content_sha256,
        )
    except ScanVersionChanged as exc:
        raise root._review_http_error(
            "target_changed", "The selected media file changed during import", 409
        ) from exc


def _run_edl_import(
    db: Session,
    *,
    content: bytes,
    options,
    source_name: str,
    caller=None,
    prepared=None,
    staged_version=None,
):
    """Import EDL content for a resolved target and return the new review."""
    if caller is not None:
        prepared = capture_target(caller, options.target)
        if options.stage_for_verification:
            staged_version = _capture_staged_version(caller, prepared)
        return caller.present(
            caller.create(
                prepared.media,
                lambda session: _run_edl_import(
                    session,
                    content=content,
                    options=options,
                    source_name=source_name,
                    prepared=prepared,
                    staged_version=staged_version,
                ),
                capability=C.REVIEWS_IMPORT_UPLOAD,
                capabilities=CONTENT_EDITS,
            )
        )
    if prepared is None:
        media, media_source_id, catalog_document = _import_target_media(db, options.target)
    else:
        media = prepared.store(db)
        media_source_id, catalog_document = prepared.source.media_source_id, prepared.document
    assert media is not None
    media_path = media.path
    if not media_path:
        raise ValueError("The selected media file is unavailable")
    try:
        resolution = resolve_local_path(db, media_path)
        local_path = Path(resolution.local_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError("The selected media file is unavailable") from exc
    if not local_path.is_file() or not os.access(local_path, os.R_OK):
        raise ValueError("The selected media file is unavailable")
    try:
        duration = root.get_video_duration(str(local_path))
    except (OSError, RuntimeError, ValueError):
        duration = None
    if catalog_document.material_end_seconds is None and duration is not None:
        catalog_document = catalog_document.model_copy(update={"material_end_seconds": duration})
    from scanner.formats.edl import parse_edl_content

    version_id = _store_staged_version(db, prepared, staged_version)
    parsed = parse_edl_content(content, duration_seconds=None if version_id else duration)
    return import_edl_as_saved_review(
        db,
        media_item=media,
        resolved_local_path=str(local_path),
        media_source_id=media_source_id,
        source_name=_safe_import_basename(source_name),
        review_name=options.name,
        parsed=parsed,
        duration_seconds=duration,
        catalog_mcf_document=catalog_document,
        verification_version_id=version_id,
    )


def _run_mcf_import(
    db: Session,
    *,
    content: bytes,
    options,
    source_name: str,
    caller=None,
    prepared=None,
    staged_version=None,
):
    """Parse and import MCF content with synchronization and metadata checks."""
    if caller is not None:
        prepared = capture_target(caller, options.target)
        capabilities = CONTENT_EDITS + (
            (C.REVIEWS_CREDITS_EDIT_OWN,) if options.accepted_credits else ()
        )
        scan_ids = (options.accepted_credits.scan_id,) if options.accepted_credits else ()
        if options.stage_for_verification:
            staged_version = _capture_staged_version(
                caller, prepared, capabilities=capabilities, scan_ids=scan_ids
            )
        return caller.present(
            caller.create(
                prepared.media,
                lambda session: _run_mcf_import(
                    session,
                    content=content,
                    options=options,
                    source_name=source_name,
                    prepared=prepared,
                    staged_version=staged_version,
                ),
                capability=C.REVIEWS_IMPORT_UPLOAD,
                capabilities=capabilities,
                scan_ids=scan_ids,
            )
        )
    if prepared is None:
        media, media_source_id, catalog_document = _import_target_media(db, options.target)
    else:
        media = prepared.store(db)
        media_source_id, catalog_document = prepared.source.media_source_id, prepared.document
    assert media is not None
    media_path = media.path
    if not media_path:
        raise _ImportMediaUnavailable("The selected media file is unavailable")
    try:
        resolution = resolve_local_path(db, media_path)
        local_path = Path(resolution.local_path).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise _ImportMediaUnavailable("The selected media file is unavailable") from exc
    if not local_path.is_file() or not os.access(local_path, os.R_OK):
        raise _ImportMediaUnavailable("The selected media file is unavailable")
    try:
        duration = root.get_video_duration(str(local_path))
    except (OSError, RuntimeError, ValueError):
        duration = None
    if catalog_document.material_end_seconds is None and duration is not None:
        catalog_document = catalog_document.model_copy(update={"material_end_seconds": duration})
    recovered = parse_mcf_content_with_recovery(content)
    if recovered.issues and not options.ignore_errors:
        raise _McfImportIssues(recovered.issues)
    parsed = recovered.parsed
    version_id = _store_staged_version(db, prepared, staged_version)
    return import_mcf_as_saved_review(
        db,
        media_item=media,
        resolved_local_path=str(local_path),
        media_source_id=media_source_id,
        source_name=_safe_mcf_import_basename(source_name),
        review_name=options.name,
        parsed=parsed,
        catalog_mcf_document=catalog_document,
        duration_seconds=duration,
        synchronization=options.synchronization,
        mcf_material_end_seconds=options.mcf_material_end_seconds,
        accepted_credits=options.accepted_credits,
        verification_version_id=version_id,
    )


def _mcf_parse_http_error(exc: McfParseError, content: bytes) -> HTTPException:
    """Translate MCF parser issues into a line-aware HTTP response."""
    return root._review_http_error(
        exc.code,
        exc.message,
        413 if exc.code == "mcf_import_too_large" else 422,
        line_number=exc.line_number,
        line_text=(
            None
            if exc.code == "mcf_import_too_large"
            else _import_error_line(content, exc.line_number)
        ),
    )


def _mcf_import_issues_http_error(exc: _McfImportIssues) -> HTTPException:
    """Return structured issues for an MCF import requiring user choice."""
    return root._review_http_error(
        "mcf_import_issues",
        "Issues were found in the MCF file",
        422,
        import_issues=[
            McfImportIssue(
                code=issue.code,
                message=issue.message,
                line_number=issue.line_number,
                line_text=issue.line_text,
            )
            for issue in exc.issues
        ],
    )


@router.post(
    "/api/mcf-imports/upload",
    response_model=McfImportResult,
    status_code=201,
    responses=_import_error_responses(400, 409, 413, 502),
)
async def api_import_mcf_upload(
    file: UploadFile = File(...),
    options: str = Form(
        ...,
        description="JSON-encoded McfImportOptions sent as a text field.",
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    caller: ReviewAccess = Depends(review_caller),
):
    """Import an uploaded MCF for the requested media target."""
    db = caller.db
    try:
        parsed_options = TypeAdapter(McfImportOptions).validate_python(json.loads(options))
        if not file.filename:
            raise ValueError("Uploaded file must have an .mcf filename")
        safe_name = _safe_mcf_import_basename(file.filename)
        if Path(safe_name).suffix.lower() != ".mcf":
            raise ValueError("Uploaded file must have an .mcf filename")
        content = await file.read(MAX_MCF_CONTENT_BYTES + 1)
        if len(content) > MAX_MCF_CONTENT_BYTES:
            raise McfParseError("mcf_import_too_large", "MCF content exceeds the size limit")
        return await drained_thread(
            _run_mcf_import,
            db,
            content=content,
            options=parsed_options,
            source_name=safe_name,
            caller=caller,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        db.rollback()
        _refuse_another_source(exc)
        raise root._review_http_error(
            "invalid_mcf_document", "Import options are invalid", 422
        ) from exc
    except McfParseError as exc:
        db.rollback()
        raise _mcf_parse_http_error(exc, content) from exc
    except _McfImportIssues as exc:
        db.rollback()
        raise _mcf_import_issues_http_error(exc) from exc
    except _ImportMediaUnavailable as exc:
        db.rollback()
        raise root._review_http_error("media_unavailable", str(exc), 409) from exc
    except MediaRuntimeUnavailable as exc:
        db.rollback()
        raise root._review_http_error("media_runtime_unavailable", str(exc), 409) from exc
    except McfImportTimingError as exc:
        db.rollback()
        raise root._review_http_error(
            exc.code,
            str(exc),
            422,
            document_fields=exc.document_fields,
        ) from exc
    except CreditsAcceptanceError as exc:
        db.rollback()
        raise root._review_http_error(exc.code, str(exc), exc.status_code) from exc
    except ValueError as exc:
        db.rollback()
        raise root._review_http_error(
            "invalid_mcf_document", "MCF import request is invalid", 422
        ) from exc


@router.post(
    "/api/edl-imports/upload",
    response_model=EdlImportResult,
    status_code=201,
    responses=_import_error_responses(400, 409, 413, 502),
)
async def api_import_edl_upload(
    file: UploadFile = File(...),
    options: str = Form(
        ...,
        description="JSON-encoded EdlImportOptions sent as a text field.",
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    caller: ReviewAccess = Depends(review_caller),
):
    """Import an uploaded EDL for the requested media target."""
    db = caller.db
    try:
        parsed_options = TypeAdapter(EdlImportOptions).validate_python(json.loads(options))
        safe_name = _safe_import_basename(file.filename)
        if Path(safe_name).suffix.lower() != ".edl":
            raise ValueError("Uploaded file must have an .edl filename")
        content = await file.read(1_048_577)
        if len(content) > 1_048_576:
            raise EdlParseError("edl_import_too_large", "EDL content exceeds the size limit")
        return await drained_thread(
            _run_edl_import,
            db,
            content=content,
            options=parsed_options,
            source_name=safe_name,
            caller=caller,
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        db.rollback()
        _refuse_another_source(exc)
        raise root._review_http_error(
            "invalid_edl_import", "Import options are invalid", 422
        ) from exc
    except EdlParseError as exc:
        db.rollback()
        status_code = 413 if exc.code == "edl_import_too_large" else 422
        raise root._review_http_error(
            exc.code,
            exc.message,
            status_code,
            line_number=exc.line_number,
            line_text=_import_error_line(content, exc.line_number),
        ) from exc
    except MediaRuntimeUnavailable as exc:
        db.rollback()
        raise root._review_http_error("media_runtime_unavailable", str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        if "No supported EDL" in str(exc):
            raise root._review_http_error("no_supported_edl_actions", str(exc), 422) from exc
        raise root._review_http_error("invalid_edl_import", str(exc), 422) from exc
