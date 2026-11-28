"""Upload VideoSkip files as local evidence awaiting timing verification."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import ValidationError

from scanner import api as root
from scanner.formats.videoskip import MAX_CONTENT_BYTES, VideoSkipParseError, parse_videoskip
from scanner.modeling.model_reviews import VideoSkipImportOptions, VideoSkipImportResult
from scanner.modeling.model_videoskip import VideoSkipSource
from scanner.persistence.repository.review_access import ReviewAccess

from .drained_threads import drained_thread
from .review_access import review_caller
from .review_import_helpers_api import _refuse_another_source, _safe_import_basename
from .review_import_routes_api import _run_edl_import

router = APIRouter()


def _stage_upload(caller, content: bytes, filename: str, options: VideoSkipImportOptions):
    parsed = parse_videoskip(content)
    actions = parsed.to_import()
    if not actions.filters:
        raise VideoSkipParseError(
            "no_supported_videoskip_actions",
            "No supported VideoSkip skip or mute cues were found",
            parsed.issues[0].line_number if parsed.issues else None,
        )
    source = VideoSkipSource(
        filename=filename,
        sha256=parsed.sha256,
        sync=parsed.sync,
        **options.attribution.model_dump(),
    )
    result = _run_edl_import(
        caller.db,
        content=content,
        options=options,
        source_name=filename,
        caller=caller,
        parsed_actions=actions,
        videoskip_source=source,
    )
    return VideoSkipImportResult(
        scan=result.scan, issue_count=len(parsed.issues), issues=parsed.issues[:100]
    )


@router.post(
    "/api/videoskip-imports/upload",
    response_model=VideoSkipImportResult,
    status_code=201,
    responses=root._import_error_responses(400, 409, 413, 502),
)
async def api_import_videoskip_upload(
    file: UploadFile = File(...),
    options: str = Form(
        ...,
        description="JSON-encoded VideoSkipImportOptions sent as a text field.",
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    caller: ReviewAccess = Depends(review_caller),
):
    """Stage the uploaded skip and mute cues without setting a verified timeline."""
    try:
        parsed_options = VideoSkipImportOptions.model_validate(json.loads(options))
        filename = _safe_import_basename(file.filename, format_label="VideoSkip")
        if Path(filename).suffix.lower() != ".skp":
            raise ValueError("Uploaded file must have a .skp filename")
        content = await file.read(MAX_CONTENT_BYTES + 1)
        return await drained_thread(_stage_upload, caller, content, filename, parsed_options)
    except (json.JSONDecodeError, ValidationError) as exc:
        caller.db.rollback()
        _refuse_another_source(exc)
        raise root._review_http_error(
            "invalid_videoskip_import", "VideoSkip import options are invalid", 422
        ) from exc
    except VideoSkipParseError as exc:
        caller.db.rollback()
        raise root._review_http_error(
            exc.code, str(exc), 413 if exc.code == "videoskip_too_large" else 422,
            line_number=exc.line_number,
        ) from exc
    except ValueError as exc:
        caller.db.rollback()
        raise root._review_http_error("invalid_videoskip_import", str(exc), 422) from exc
