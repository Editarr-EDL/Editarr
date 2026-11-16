"""FastAPI backend and compatibility entry point for Editarr routes."""

# Keep public route helpers available from the application entry point.
# ruff: noqa: F401

import hashlib as hashlib
import json
import logging
import math
import os
import shutil as shutil
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from typing import Literal
from urllib.parse import quote as quote
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from scanner import __version__
from scanner.credits.credits_detector import default_configuration
from scanner.db import MediaItemRow as MediaItemRow
from scanner.db import get_db, get_session_factory, init_db
from scanner.entry import scan_worker_command
from scanner.formats.edl import EdlParseError
from scanner.http.api_http import (
    PrefixRoutingMiddleware,
    RequestLoggingMiddleware,
    _default_deployment_snapshot,
    active_deployment_snapshot,
    configure_http_service,
    cors_options,
)
from scanner.http.api_key_api import router as api_key_users_router
from scanner.http.archive_api import router as archive_router
from scanner.http.audit_history_api import router as audit_history_router
from scanner.http.auth_api import router as authentication_router
from scanner.http.backup_api import router as backup_router
from scanner.http.backup_schedule_api import router as backup_schedule_router
from scanner.http.community_api import router as community_router
from scanner.http.deployment_api import router as deployment_router
from scanner.http.filter_policy_api import router as filter_policy_router
from scanner.http.jellyfin_accounts_api import router as jellyfin_accounts_router
from scanner.http.jellyfin_login_api import router as jellyfin_login_router
from scanner.http.jellyfin_quick_connect_api import router as jellyfin_quick_connect_router
from scanner.http.openapi import configure_api_documentation
from scanner.http.restart_api import router as restart_router
from scanner.http.review_policy_errors import install_review_policy_errors
from scanner.http.setup_restore_api import router as setup_restore_router
from scanner.http.users_routes import ROUTERS as USERS_ROUTERS
from scanner.integrations.jellyfin import JellyfinClient, JellyfinError
from scanner.integrations.jellyfin_server_url import normalize_server_url as normalize_server_url
from scanner.mcf import (
    MAX_CONTENT_BYTES as MAX_MCF_CONTENT_BYTES,
)
from scanner.mcf import (
    McfParseError,
    McfParseIssue,
    parse_mcf_content_with_recovery,
)
from scanner.model_acquisition import (
    AcquisitionCancelled,
    AcquisitionError,
    get_acquisition_service,
)
from scanner.modeling.model_acquisition_authority import request_server
from scanner.models import MCF_CATEGORY_FAMILY_LABELS as MCF_CATEGORY_FAMILY_LABELS
from scanner.models import MCF_CATEGORY_REGISTRY as MCF_CATEGORY_REGISTRY
from scanner.models import (
    CreditsAcceptanceRequest,
    CreditsAcceptanceResult,
    CreditsDetectionStartRequest,
    CreditsDetectionStartResult,
    CreditsModelConsent,
    CreditsModelConsentRequest,
    EdlImportOptions,
    EdlImportResult,
    ExportFormat,
    ExportStatus,
    JellyfinMediaItem,
    McfImportIssue,
    McfImportOptions,
    McfImportResult,
    RequestValidationErrorResponse,
    ReviewError,
    ReviewErrorDetail,
    ReviewMcfDocument,
    Scan,
    ScanMode,
    ScanName,
    ScanQueueItem,
)
from scanner.models import FilterCandidateSearchResult as FilterCandidateSearchResult
from scanner.models import FilterDefaultsApplyRequest as FilterDefaultsApplyRequest
from scanner.models import FilterDefaultsApplyResult as FilterDefaultsApplyResult
from scanner.models import FilterDefaultsSettings as FilterDefaultsSettings
from scanner.models import FilterDefaultsUpdate as FilterDefaultsUpdate
from scanner.models import JellyfinLibrary as JellyfinLibrary
from scanner.models import JellyfinMediaPage as JellyfinMediaPage
from scanner.models import JellyfinSettings as JellyfinSettings
from scanner.models import JellyfinSettingsUpdate as JellyfinSettingsUpdate
from scanner.models import LibraryReviewCreate as LibraryReviewCreate
from scanner.models import McfMetadataSeedCandidate as McfMetadataSeedCandidate
from scanner.models import McfPreflight as McfPreflight
from scanner.models import McfRegistry as McfRegistry
from scanner.models import McfWorkType as McfWorkType
from scanner.models import PathMapping as PathMapping
from scanner.models import PathMappingCreate as PathMappingCreate
from scanner.models import PathMappingReorder as PathMappingReorder
from scanner.models import PathMappingUpdate as PathMappingUpdate
from scanner.models import PathResolution as PathResolution
from scanner.models import ReviewDuplicate as ReviewDuplicate
from scanner.models import ReviewExportRequest as ReviewExportRequest
from scanner.models import ReviewExportResponse as ReviewExportResponse
from scanner.models import ReviewRename as ReviewRename
from scanner.models import ReviewRetimeRequest as ReviewRetimeRequest
from scanner.models import ReviewRetimeResult as ReviewRetimeResult
from scanner.models import ReviewSave as ReviewSave
from scanner.models import SavedReview as SavedReview
from scanner.models import SavedReviewSummary as SavedReviewSummary
from scanner.models import ScanAssociationUpdate as ScanAssociationUpdate
from scanner.models import ScanCapabilities as ScanCapabilities
from scanner.models import ScanFindings as ScanFindings
from scanner.models import ScanMatch as ScanMatch
from scanner.models import ScanMatchSearch as ScanMatchSearch
from scanner.models import ScanModelCapability as ScanModelCapability
from scanner.models import ScanNameUpdate as ScanNameUpdate
from scanner.models import ScanQueueReorder as ScanQueueReorder
from scanner.models import ScanReferences as ScanReferences
from scanner.pipeline.frames import get_video_duration
from scanner.repository import (
    CreditsAcceptanceError,
    CreditsRouteError,
    McfImportTimingError,
    MediaRuntimeUnavailable,
    accept_credits_detection,
    create_scan,
    delete_library_item_export,
    enqueue_scan,
    find_active_credits_detection,
    get_active_scan_queue_item,
    get_credits_detection,
    get_jellyfin_credentials,
    get_next_scan_queue,
    get_saved_review,
    get_scan,
    get_scan_queue_item_by_scan_id,
    import_edl_as_saved_review,
    import_mcf_as_saved_review,
    is_scan_cancellation_requested,
    list_scan_queue,
    mark_orphaned_scan_workers,
    mark_scan_orphaned,
    media_item_export_status,
    record_jellyfin_connection,
    record_scan_worker_process,
    recover_cancelled_scan_queue_items,
    recover_interrupted_credits_model_acquisitions,
    recover_terminal_scan_queue_items,
    remove_scan_queue_item,
    render_credits_detection_preview,
    reserve_scan_worker,
    resolve_local_path,
    scan_worker_process,
    scanning_scan_worker_processes,
    update_credits_detection_configuration,
    update_scan_progress,
    update_scan_status,
    upsert_media_item,
)
from scanner.repository import FilterSettingsRevisionConflict as FilterSettingsRevisionConflict
from scanner.repository import IncompleteMcfReview as IncompleteMcfReview
from scanner.repository import McfMetadataSourceRequired as McfMetadataSourceRequired
from scanner.repository import ReviewMediaUnavailable as ReviewMediaUnavailable
from scanner.repository import ReviewMembershipError as ReviewMembershipError
from scanner.repository import ReviewRetimeError as ReviewRetimeError
from scanner.repository import ReviewRevisionConflict as ReviewRevisionConflict
from scanner.repository import ScanReferencedError as ScanReferencedError
from scanner.repository import associate_scans_with_media_item as associate_scans_with_media_item
from scanner.repository import (
    associated_scan_ids_for_media_item as associated_scan_ids_for_media_item,
)
from scanner.repository import cancel_scan_queue_item as cancel_scan_queue_item
from scanner.repository import create_path_mapping as create_path_mapping
from scanner.repository import create_saved_review as create_saved_review
from scanner.repository import delete_path_mapping as delete_path_mapping
from scanner.repository import delete_saved_review as delete_saved_review
from scanner.repository import delete_scan as delete_scan
from scanner.repository import duplicate_saved_review as duplicate_saved_review
from scanner.repository import export_saved_review_edl as export_saved_review_edl
from scanner.repository import export_saved_review_mcf as export_saved_review_mcf
from scanner.repository import find_filter_candidates as find_filter_candidates
from scanner.repository import get_jellyfin_settings as get_jellyfin_settings
from scanner.repository import get_scan_findings as get_scan_findings
from scanner.repository import get_scan_references as get_scan_references
from scanner.repository import (
    get_structured_filter_defaults_settings as get_structured_filter_defaults_settings,
)
from scanner.repository import library_item_export_statuses as library_item_export_statuses
from scanner.repository import (
    library_item_version_export_statuses_for_items as library_item_version_export_statuses_for_items,  # noqa: E501
)
from scanner.repository import list_path_mappings as list_path_mappings
from scanner.repository import list_saved_reviews as list_saved_reviews
from scanner.repository import list_scan_history as list_scan_history
from scanner.repository import list_scans_for_library_item as list_scans_for_library_item
from scanner.repository import preflight_saved_review_mcf as preflight_saved_review_mcf
from scanner.repository import rename_saved_review as rename_saved_review
from scanner.repository import rename_scan as rename_scan
from scanner.repository import (
    render_saved_review_filter_preview as render_saved_review_filter_preview,
)
from scanner.repository import reorder_path_mappings as reorder_path_mappings
from scanner.repository import reorder_scan_queue as reorder_scan_queue
from scanner.repository import resume_orphaned_scan as resume_orphaned_scan
from scanner.repository import retime_saved_review_draft as retime_saved_review_draft
from scanner.repository import save_saved_review as save_saved_review
from scanner.repository import update_filter_defaults_settings as update_filter_defaults_settings
from scanner.repository import update_jellyfin_settings as update_jellyfin_settings
from scanner.repository import update_path_mapping as update_path_mapping
from scanner.runtime.application_paths import application_url as application_url
from scanner.runtime.process_control import (
    GRACEFUL_STOP_TIMEOUT,
    ProcessControl,
    WorkerIdentity,
    cleanup_worker_control_endpoint,
)
from scanner.runtime.production_frontend import ProductionFrontendMiddleware, configure_frontend
from scanner.runtime.runtime_logging import configure_runtime_logging, set_scanner_log_level
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.security.access.request_access import install_access_errors
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import seal_plaintext_api_keys
from scanner.security.audit.authentication import (
    sessions_revoked_by_key_change as record_key_change_revocation,
)
from scanner.security.audit.store import run_retention as run_audit_retention
from scanner.security.auth_audit import AuditContext, AuthenticationAuditLogger, new_request_id
from scanner.security.auth_crypto import load_csrf_encryption_key
from scanner.security.auth_policy import AuthPolicyMiddleware
from scanner.security.auth_repository import (
    cleanup_expired_sessions,
    revoke_sessions_with_other_csrf_key_id,
    setup_required,
)
from scanner.settings.capabilities import (
    DEFAULT_AUDIO_FILTER,
    DEFAULT_AUDIO_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_AUDIO_FILTER_SELECTIONS,
    SUPPORTED_AUDIO_MODELS,
    SUPPORTED_QWEN_MODELS,
    SUPPORTED_VIDEO_MODELS,
    qwen_enrichment_capability,
    require_available,
)
from scanner.settings.capabilities import QWEN_MODEL_DISPLAY_NAMES as QWEN_MODEL_DISPLAY_NAMES
from scanner.settings.capabilities import SUPPORTED_AUDIO_FILTERS as SUPPORTED_AUDIO_FILTERS
from scanner.settings.capabilities import current_environment as current_environment
from scanner.settings.capabilities import model_capabilities as model_capabilities
from scanner.settings.config import settings
from scanner.settings.deployment_config import (
    DeploymentSnapshot,
    DeploymentStore,
    resolve_deployment_snapshot,
)
from scanner.settings.diagnostics import (
    DiagnosticsError,
    DiagnosticsPreferences,
    load_diagnostics_preferences,
    read_log_tail,
    save_diagnostics_preferences,
)
from scanner.settings.filter_defaults import apply_filter_defaults as apply_filter_defaults

logger = logging.getLogger(__name__)
PROCESS_CONTROL = ProcessControl()


def _model_tasks() -> dict[str, Thread]:
    """Return the per-process prefetch registry without persisting transient work."""

    tasks = getattr(app.state, "model_prefetch_tasks", None)
    if tasks is None:
        tasks = {}
        app.state.model_prefetch_tasks = tasks
        app.state.model_prefetch_lock = Lock()
    return tasks


def _model_task_lock() -> Lock:
    _model_tasks()
    return app.state.model_prefetch_lock


_INITIAL_DEPLOYMENT_SNAPSHOT = _default_deployment_snapshot()


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    with request_server():
        startup()
        try:
            yield
        finally:
            shutdown()


app = FastAPI(
    title="Editarr",
    version=__version__,
    root_path=_INITIAL_DEPLOYMENT_SNAPSHOT.prefix,
    lifespan=lifespan,
)
configure_api_documentation(app)
app.state.deployment_snapshot = _INITIAL_DEPLOYMENT_SNAPSHOT
app.state.frontend_assets = None
install_access_errors(app)
install_review_policy_errors(app)
app.add_middleware(CORSMiddleware, **cors_options(_INITIAL_DEPLOYMENT_SNAPSHOT))
app.add_middleware(AuthPolicyMiddleware, configuration=settings)
app.include_router(authentication_router)
app.include_router(jellyfin_login_router)
app.include_router(jellyfin_quick_connect_router)
app.include_router(api_key_users_router)
app.include_router(audit_history_router)
app.include_router(archive_router)
app.include_router(filter_policy_router)
for users_router in USERS_ROUTERS:
    app.include_router(users_router)
app.include_router(jellyfin_accounts_router)
app.include_router(backup_router)
app.include_router(backup_schedule_router)
app.include_router(community_router)
app.include_router(setup_restore_router)
app.include_router(deployment_router)
app.include_router(restart_router)


@app.exception_handler(RequestValidationError)
async def scan_name_validation_error(request: Request, exc: RequestValidationError):
    """Return useful validation locations without reflecting request values.

    Pydantic includes the complete failing input by default.  That can expose a
    nested scanner-host path, even when the validation error itself belongs to
    the enclosing object.
    """
    path = request.url.path.rstrip("/")
    if path.endswith("/api/credits-detections"):
        return JSONResponse(
            status_code=400,
            content={
                "detail": ReviewErrorDetail(
                    code="invalid_credits_target",
                    message="Credits detection request is invalid",
                ).model_dump(mode="json")
            },
        )
    if "/api/credits-detections/" in path and path.endswith("/accept"):
        boundary_error = any(
            error.get("loc", ())[-1:] == ("accepted_boundary_seconds",) for error in exc.errors()
        )
        return JSONResponse(
            status_code=422 if boundary_error else 400,
            content={
                "detail": ReviewErrorDetail(
                    code=(
                        "invalid_credits_boundary" if boundary_error else "invalid_credits_target"
                    ),
                    message=(
                        "Accepted credits boundary is invalid"
                        if boundary_error
                        else "Credits detection target is invalid"
                    ),
                ).model_dump(mode="json")
            },
        )
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {key: error[key] for key in ("type", "loc", "msg") if key in error}
                for error in exc.errors()
            ]
        },
    )


def _review_error_responses(*status_codes: int):
    return {status_code: {"model": ReviewError} for status_code in status_codes}


def _import_error_responses(*status_codes: int):
    responses = _review_error_responses(*status_codes)
    responses[422] = {"model": ReviewError | RequestValidationErrorResponse}
    return responses


# Prefix routing must remain outermost: the public static layer below it must never
# create a root copy of a prefixed deployment.  The frontend layer sits ahead of
# authentication only for its intentionally tiny shell/asset surface.
app.add_middleware(ProductionFrontendMiddleware)
app.add_middleware(PrefixRoutingMiddleware)
app.add_middleware(RequestLoggingMiddleware)


SCAN_QUEUE_ACTIVATION_LOCK = Lock()
SCAN_QUEUE_PROCESSING_ACTIVE = False


from scanner.http import api_lifecycle  # noqa: E402 - share initialized service state

_secure_data_directory = api_lifecycle._secure_data_directory
_recover_and_start_scan_queue_processing = api_lifecycle._recover_and_start_scan_queue_processing
startup = api_lifecycle.startup
shutdown = api_lifecycle.shutdown
app.state.activate_scan_queue_processing = _recover_and_start_scan_queue_processing


from scanner.http import api_system  # noqa: E402 - register after application construction

app.include_router(api_system.router)

ScanOptions = api_system.ScanOptions
ScanRequest = api_system.ScanRequest
PathResolutionRequest = api_system.PathResolutionRequest
DiagnosticLogSettings = api_system.DiagnosticLogSettings
DiagnosticLogTail = api_system.DiagnosticLogTail
api_get_diagnostic_log_settings = api_system.api_get_diagnostic_log_settings
api_update_diagnostic_log_settings = api_system.api_update_diagnostic_log_settings
api_get_diagnostic_log_tail = api_system.api_get_diagnostic_log_tail


SCAN_QUEUE_WORKER_LOCK = Lock()
SCAN_QUEUE_WORKER_RUNNING = False
SCAN_QUEUE_WORKER_THREAD: Thread | None = None
SCAN_QUEUE_STOP_EVENT = Event()
SCAN_QUEUE_WAKE_EVENT = Event()
LIBRARY_ITEMS_CACHE_LOCK = Lock()
LIBRARY_ITEMS_CACHE: dict[tuple[str, str], list[JellyfinMediaItem]] = {}
JELLYFIN_ITEM_CACHE: dict[tuple[str, str], JellyfinMediaItem] = {}


from scanner.http import api_queue  # noqa: E402 - share initialized application state

_worker_identity = api_queue._worker_identity
_terminate_scan_worker = api_queue._terminate_scan_worker
_recover_failed_scan_launch = api_queue._recover_failed_scan_launch
_stop_discovered_worker = api_queue._stop_discovered_worker
_launch_scan_worker = api_queue._launch_scan_worker
_admit_scan_job = api_queue._admit_scan_job
_scan_queue_worker = api_queue._scan_queue_worker
_start_scan_queue_worker_if_needed = api_queue._start_scan_queue_worker_if_needed


from scanner.http import catalog_api  # noqa: E402 - register after shared API state exists

app.include_router(catalog_api.router)

_test_jellyfin_connection = catalog_api._test_jellyfin_connection
_candidate_jellyfin_credentials = catalog_api._candidate_jellyfin_credentials
api_get_jellyfin_settings = catalog_api.api_get_jellyfin_settings
api_test_jellyfin_settings = catalog_api.api_test_jellyfin_settings
api_update_jellyfin_settings = catalog_api.api_update_jellyfin_settings
api_disconnect_jellyfin = catalog_api.api_disconnect_jellyfin
api_find_filter_candidates = catalog_api.api_find_filter_candidates
api_associate_filter_candidates = catalog_api.api_associate_filter_candidates
api_get_filter_defaults_settings = catalog_api.api_get_filter_defaults_settings
api_update_filter_defaults_settings = catalog_api.api_update_filter_defaults_settings
api_apply_filter_defaults = catalog_api.api_apply_filter_defaults
api_list_jellyfin_libraries = catalog_api.api_list_jellyfin_libraries
_library_sort_value = catalog_api._library_sort_value
_normalized_title = catalog_api._normalized_title
_library_browse_items = catalog_api._library_browse_items
_with_library_item_state = catalog_api._with_library_item_state
_cached_library_items = catalog_api._cached_library_items
_cached_jellyfin_item = catalog_api._cached_jellyfin_item
api_list_jellyfin_library_items = catalog_api.api_list_jellyfin_library_items
api_get_jellyfin_library_item = catalog_api.api_get_jellyfin_library_item
api_list_jellyfin_library_item_children = catalog_api.api_list_jellyfin_library_item_children
api_list_jellyfin_series_seasons = catalog_api.api_list_jellyfin_series_seasons


from scanner.http import scan_create_api, scan_jobs_api  # noqa: E402 - after shared API state

app.include_router(scan_create_api.router)
app.include_router(scan_jobs_api.router)


from scanner.http.audio_tracks_api import router as audio_tracks_router  # noqa: E402

app.include_router(audio_tracks_router)

api_enqueue_scan = scan_create_api.api_enqueue_scan
api_list_scan_queue = scan_jobs_api.api_list_scan_queue


from scanner.http import review_api  # noqa: E402 - register after shared API state exists

app.include_router(review_api.router)

_review_http_error = review_api._review_http_error
_review_mutation_error = review_api._review_mutation_error
_catalog_review_media = review_api._catalog_review_media
_mcf_work_type = review_api._mcf_work_type
_list_catalog_reviews = review_api._list_catalog_reviews
_refresh_saved_review_target = review_api._refresh_saved_review_target
api_list_jellyfin_reviews = review_api.api_list_jellyfin_reviews
api_create_jellyfin_review = review_api.api_create_jellyfin_review
api_get_saved_review = review_api.api_get_saved_review
api_save_saved_review = review_api.api_save_saved_review
api_retime_saved_review = review_api.api_retime_saved_review
api_rename_saved_review = review_api.api_rename_saved_review
api_duplicate_saved_review = review_api.api_duplicate_saved_review
api_delete_saved_review = review_api.api_delete_saved_review
api_saved_review_mute_preview = review_api.api_saved_review_mute_preview
api_saved_review_video_preview = review_api.api_saved_review_video_preview
api_export_saved_review = review_api.api_export_saved_review
api_preflight_saved_review_mcf = review_api.api_preflight_saved_review_mcf
api_export_saved_review_mcf = review_api.api_export_saved_review_mcf


from scanner.http import review_import_api  # noqa: E402 - register after shared API state exists

app.include_router(review_import_api.router)

_safe_import_basename = review_import_api._safe_import_basename
_safe_mcf_import_basename = review_import_api._safe_mcf_import_basename
_ImportMediaUnavailable = review_import_api._ImportMediaUnavailable
_McfImportIssues = review_import_api._McfImportIssues
_import_error_line = review_import_api._import_error_line
_import_target_media = review_import_api._import_target_media
_credits_target_media = review_import_api._credits_target_media
_credits_model_consent = review_import_api._credits_model_consent
_credits_consent_if_required = review_import_api._credits_consent_if_required
api_start_credits_detection = review_import_api.api_start_credits_detection
api_get_credits_detection = review_import_api.api_get_credits_detection
_run_credits_model_acquisition = review_import_api._run_credits_model_acquisition
api_credits_detection_model_consent = review_import_api.api_credits_detection_model_consent
api_accept_credits_detection = review_import_api.api_accept_credits_detection
api_credits_detection_clip = review_import_api.api_credits_detection_clip
_run_edl_import = review_import_api._run_edl_import
_run_mcf_import = review_import_api._run_mcf_import
_mcf_parse_http_error = review_import_api._mcf_parse_http_error
_mcf_import_issues_http_error = review_import_api._mcf_import_issues_http_error
api_import_mcf_upload = review_import_api.api_import_mcf_upload
api_import_edl_upload = review_import_api.api_import_edl_upload


from scanner.http import scan_catalog_api  # noqa: E402 - register after shared API state exists

app.include_router(scan_catalog_api.router)

api_reorder_scan_queue = scan_jobs_api.api_reorder_scan_queue
api_cancel_scan_queue_item = scan_jobs_api.api_cancel_scan_queue_item
api_resume_orphaned_scan = scan_jobs_api.api_resume_orphaned_scan
api_list_scan_history = scan_jobs_api.api_list_scan_history
api_rename_scan = scan_jobs_api.api_rename_scan
api_get_scan_references = scan_jobs_api.api_get_scan_references
api_delete_scan = scan_jobs_api.api_delete_scan
api_get_scan_findings = scan_jobs_api.api_get_scan_findings
api_list_library_item_scans = scan_catalog_api.api_list_library_item_scans
api_library_item_exports = scan_catalog_api.api_library_item_exports
api_delete_library_item_export = scan_catalog_api.api_delete_library_item_export
api_library_item_primary_image = scan_catalog_api.api_library_item_primary_image
api_list_path_mappings = scan_catalog_api.api_list_path_mappings
api_create_path_mapping = scan_catalog_api.api_create_path_mapping
api_update_path_mapping = scan_catalog_api.api_update_path_mapping
api_delete_path_mapping = scan_catalog_api.api_delete_path_mapping
api_reorder_path_mappings = scan_catalog_api.api_reorder_path_mappings
api_resolve_path_mapping = scan_catalog_api.api_resolve_path_mapping
api_capabilities = scan_catalog_api.api_capabilities
api_mcf_registry = scan_catalog_api.api_mcf_registry


from scanner.http import model_storage_api  # noqa: E402 - register after the application exists

app.include_router(model_storage_api.router)

ModelActionResult = model_storage_api.ModelActionResult
ModelPrefetchRequest = model_storage_api.ModelPrefetchRequest
ModelStorageStatus = model_storage_api.ModelStorageStatus
artifact_ids_for_backend = model_storage_api.artifact_ids_for_backend
_model_storage_status = model_storage_api._model_storage_status
_model_error = model_storage_api._model_error
_model_code_error = model_storage_api._model_code_error
_backend_is_available = model_storage_api._backend_is_available
_active_model_backends = model_storage_api._active_model_backends
api_model_storage = model_storage_api.api_model_storage
api_prefetch_model = model_storage_api.api_prefetch_model
api_cancel_model_download = model_storage_api.api_cancel_model_download
api_remove_model = model_storage_api.api_remove_model
