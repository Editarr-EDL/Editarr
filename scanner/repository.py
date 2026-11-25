"""Repository entry point for scans, reviews, and persistent settings."""

# Expose repository operations from one public import path.
# ruff: noqa: F401, E402

import json as json
import math
import os
import re as re
import shutil as shutil
import tempfile as tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath as PurePosixPath
from pathlib import PureWindowsPath as PureWindowsPath

from sqlalchemy import func, select, update
from sqlalchemy import or_ as or_
from sqlalchemy.orm import Session

from scanner.db import CreditsDetectionRunRow as CreditsDetectionRunRow
from scanner.db import FilterSelectionRuleRow as FilterSelectionRuleRow
from scanner.db import FilterSettingsRow as FilterSettingsRow
from scanner.db import JellyfinSettingsRow as JellyfinSettingsRow
from scanner.db import (
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewMcfDocumentRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMediaItemAssociationRow,
    ScanRow,
)
from scanner.db import PathMappingRow as PathMappingRow
from scanner.db import ScanMcfDocumentRow as ScanMcfDocumentRow
from scanner.db import ScanQueueRow as ScanQueueRow
from scanner.formats.edl import ParsedEdl as ParsedEdl
from scanner.formats.edl import export_edl_content
from scanner.mcf import (
    MAX_LINE_CODEPOINTS as MAX_LINE_CODEPOINTS,
)
from scanner.mcf import (
    McfCueEntry,
    TimingInterval,
    export_mcf_content,
    map_affine_endpoint,
    map_affine_entries,
    map_affine_range,
)
from scanner.mcf import McfParseResult as McfParseResult
from scanner.mcf import category_label as category_label
from scanner.models import MCF_CATEGORY_TO_FAMILY as MCF_CATEGORY_TO_FAMILY
from scanner.models import (
    AcceptedCreditsProvenance,
    AcceptedCreditsReference,
    CreditsDetectionResult,
    EditAction,
    ExportFormat,
    ExportScanReference,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfMetadataSeedCandidate,
    McfPreflight,
    McfWorkType,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewDuplicate,
    ReviewExportResponse,
    ReviewFilter,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewReference,
    ReviewRename,
    ReviewRetimeRequest,
    ReviewRetimeResult,
    ReviewSave,
    ReviewScanMembership,
    ReviewScanReference,
    ReviewSynchronization,
    SavedReview,
    SavedReviewSummary,
    ScanFinding,
    ScanOrigin,
    ScanReferences,
    Severity,
)
from scanner.models import CreditsAcceptanceResult as CreditsAcceptanceResult
from scanner.models import CreditsDetection as CreditsDetection
from scanner.models import CreditsDetectionConfiguration as CreditsDetectionConfiguration
from scanner.models import EdlImportResult as EdlImportResult
from scanner.models import EdlImportWarning as EdlImportWarning
from scanner.models import ExportStatus as ExportStatus
from scanner.models import ExportSummary as ExportSummary
from scanner.models import FilterDefaultsSettings as FilterDefaultsSettings
from scanner.models import FilterDefaultsUpdate as FilterDefaultsUpdate
from scanner.models import FilterSelectionRule as FilterSelectionRule
from scanner.models import FindingCreate as FindingCreate
from scanner.models import JellyfinSettings as JellyfinSettings
from scanner.models import JellyfinSettingsUpdate as JellyfinSettingsUpdate
from scanner.models import McfImportResult as McfImportResult
from scanner.models import McfMetadataNotice as McfMetadataNotice
from scanner.models import McfPreflightIssue as McfPreflightIssue
from scanner.models import McfSeverityDefault as McfSeverityDefault
from scanner.models import PathMapping as PathMapping
from scanner.models import PathMappingCreate as PathMappingCreate
from scanner.models import PathMappingUpdate as PathMappingUpdate
from scanner.models import PathResolution as PathResolution
from scanner.models import ReviewTarget as ReviewTarget
from scanner.models import Scan as Scan
from scanner.models import ScanFindings as ScanFindings
from scanner.models import ScanMcfDocument as ScanMcfDocument
from scanner.models import ScanMode as ScanMode
from scanner.models import ScanQueueItem as ScanQueueItem
from scanner.models import normalize_scan_name as normalize_scan_name
from scanner.persistence.repository import (
    repository_catalog,
    repository_checkpoints,
    repository_credits,
    repository_exports,
    repository_provenance,
    repository_queue,
    repository_settings,
)
from scanner.persistence.repository.repository_exports import (
    _review_summary,
)
from scanner.persistence.repository.repository_mcf_preflight import (
    _mcf_document_issues,
    _mcf_filter_issues,
    _signature_value,
)
from scanner.persistence.repository.repository_paths import (
    create_path_mapping as create_path_mapping,
)
from scanner.persistence.repository.repository_paths import (
    delete_path_mapping as delete_path_mapping,
)
from scanner.persistence.repository.repository_paths import (
    list_path_mappings as list_path_mappings,
)
from scanner.persistence.repository.repository_paths import (
    reorder_path_mappings as reorder_path_mappings,
)
from scanner.persistence.repository.repository_paths import (
    resolve_local_path,
)
from scanner.persistence.repository.repository_paths import (
    update_path_mapping as update_path_mapping,
)
from scanner.persistence.repository.repository_review_exports import publish_review_sidecar
from scanner.persistence.repository.repository_rows import (
    _scan_from_row,
    _utc,
)
from scanner.pipeline.cache import compute_video_md5
from scanner.pipeline.frames import get_video_duration
from scanner.runtime.mute_preview import render_mute_preview, render_video_preview
from scanner.settings.filter_defaults import fill_filter_defaults, fill_manual_filter_defaults

cancel_scan_queue_item = repository_queue.cancel_scan_queue_item
clear_scan_worker = repository_queue.clear_scan_worker
get_active_scan_queue_item = repository_queue.get_active_scan_queue_item
get_next_scan_queue = repository_queue.get_next_scan_queue
get_scan_local_path = repository_queue.get_scan_local_path
get_scan_queue_item_by_scan_id = repository_queue.get_scan_queue_item_by_scan_id
heartbeat_scan_worker = repository_queue.heartbeat_scan_worker
is_scan_cancellation_requested = repository_queue.is_scan_cancellation_requested
mark_orphaned_scan_workers = repository_queue.mark_orphaned_scan_workers
mark_scan_checkpoint = repository_checkpoints.mark_scan_checkpoint
record_scan_stage = repository_provenance.record_scan_stage
mark_scan_orphaned = repository_queue.mark_scan_orphaned
record_scan_worker_process = repository_queue.record_scan_worker_process
recover_cancelled_scan_queue_items = repository_queue.recover_cancelled_scan_queue_items
recover_interrupted_credits_model_acquisitions = (
    repository_queue.recover_interrupted_credits_model_acquisitions
)
recover_terminal_scan_queue_items = repository_queue.recover_terminal_scan_queue_items
remove_scan_queue_item = repository_queue.remove_scan_queue_item
reorder_scan_queue = repository_queue.reorder_scan_queue
reserve_scan_worker = repository_queue.reserve_scan_worker
resume_orphaned_scan = repository_queue.resume_orphaned_scan
scan_checkpoint_cursor = repository_checkpoints.scan_checkpoint_cursor
scan_checkpoint_stages = repository_checkpoints.scan_checkpoint_stages
scan_worker_process = repository_queue.scan_worker_process
scanning_scan_worker_processes = repository_queue.scanning_scan_worker_processes
update_scan_duration = repository_queue.update_scan_duration
update_scan_progress = repository_queue.update_scan_progress
update_scan_status = repository_queue.update_scan_status
MediaRuntimeUnavailable = repository_credits.MediaRuntimeUnavailable
complete_credits_detection = repository_credits.complete_credits_detection
update_credits_detection_configuration = repository_credits.update_credits_detection_configuration
_credits_readiness_failure = repository_credits._credits_readiness_failure
FilterSettingsRevisionConflict = repository_settings.FilterSettingsRevisionConflict
get_jellyfin_credentials = repository_settings.get_jellyfin_credentials
get_jellyfin_settings = repository_settings.get_jellyfin_settings
get_structured_filter_defaults_settings = (
    repository_settings.get_structured_filter_defaults_settings
)
record_jellyfin_connection = repository_settings.record_jellyfin_connection
update_filter_defaults_settings = repository_settings.update_filter_defaults_settings
update_jellyfin_settings = repository_settings.update_jellyfin_settings


upsert_media_item = repository_catalog.upsert_media_item
rename_scan = repository_catalog.rename_scan
get_scan = repository_catalog.get_scan
enqueue_scan = repository_catalog.enqueue_scan
list_scan_queue = repository_catalog.list_scan_queue
list_scan_history = repository_catalog.list_scan_history
list_scans_for_library_item = repository_catalog.list_scans_for_library_item
associate_scans_with_media_item = repository_catalog.associate_scans_with_media_item
associated_scan_ids_for_media_item = repository_catalog.associated_scan_ids_for_media_item
create_scan_findings_batch = repository_catalog.create_scan_findings_batch
get_scan_findings = repository_catalog.get_scan_findings
find_filter_candidates = repository_catalog.find_filter_candidates
possible_candidate_scans = repository_catalog.possible_candidate_scans


library_item_export_statuses = repository_exports.library_item_export_statuses
library_item_version_export_statuses = repository_exports.library_item_version_export_statuses
library_item_version_export_statuses_for_items = (
    repository_exports.library_item_version_export_statuses_for_items
)
media_item_export_status = repository_exports.media_item_export_status
delete_library_item_export = repository_exports.delete_library_item_export
delete_library_item_edl = repository_exports.delete_library_item_edl


class ReviewRevisionConflict(ValueError):
    """The caller attempted to mutate a review from an old revision."""


class McfMetadataSourceRequired(ValueError):
    """Distinct imported metadata candidates require an explicit source."""

    def __init__(self, candidates: list[McfMetadataSeedCandidate]):
        """Attach the failure code and affected filter or document fields."""
        super().__init__("Choose which MCF metadata source to use")
        self.candidates = candidates


class IncompleteMcfReview(ValueError):
    """A persisted review cannot be exported as strict MCF."""

    def __init__(self, preflight: McfPreflight):
        """Capture candidate metadata sources for a review that needs explicit selection."""
        super().__init__("The saved review is incomplete for MCF export")
        self.preflight = preflight


class ReviewMembershipError(ValueError):
    """A scan or filter does not belong to the review target."""


class ReviewMediaUnavailable(ValueError):
    """A review target cannot be resolved to a readable physical file."""


class ReviewRetimeError(ValueError):
    """A draft retiming request failed safe validation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        filter_ids: list[str] | None = None,
        document_fields: list[str] | None = None,
    ):
        """Capture candidate metadata sources for a review that needs explicit selection."""
        super().__init__(message)
        self.code = code
        self.filter_ids = filter_ids or []
        self.document_fields = document_fields or []


class McfImportTimingError(ValueError):
    """An MCF target interval or mapped cue failed safe validation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        document_fields: list[str] | None = None,
    ):
        """Capture candidate metadata sources for a review that needs explicit selection."""
        super().__init__(message)
        self.code = code
        self.document_fields = document_fields or []


class CreditsAcceptanceError(ReviewMembershipError):
    """A detector result or edited boundary cannot be accepted safely."""

    def __init__(self, code: str, message: str, status_code: int):
        """Capture candidate metadata sources for a review that needs explicit selection."""
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class CreditsRouteError(ValueError):
    """A credits route failed with a safe, contract-defined response."""

    def __init__(self, code: str, message: str, status_code: int):
        """Capture candidate metadata sources for a review that needs explicit selection."""
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ScanReferencedError(ValueError):
    """A scan cannot be deleted while reviews or an active EDL reference it."""

    def __init__(self, message: str, references: ScanReferences):
        """Capture candidate metadata sources for a review that needs explicit selection."""
        super().__init__(message)
        self.references = references


from scanner.persistence.repository import (
    repository_review_core,  # noqa: E402 - load after review errors exist
)

_review_filter = repository_review_core._review_filter
_review_detail = repository_review_core._review_detail
_review_synchronization = repository_review_core._review_synchronization
_accepted_credits_provenance = repository_review_core._accepted_credits_provenance
_scan_is_available = repository_review_core._scan_is_available
_resolved_scan_path = repository_review_core._resolved_scan_path
_compute_video_hash = repository_review_core._compute_video_hash
_scan_matches_physical_version = repository_review_core._scan_matches_physical_version
_validated_credits_media_path = repository_review_core._validated_credits_media_path
_validate_memberships = repository_review_core._validate_memberships
_snapshot_findings = repository_review_core._snapshot_findings
list_saved_reviews = repository_review_core.list_saved_reviews
get_saved_review = repository_review_core.get_saved_review
_filter_rows_from_inputs = repository_review_core._filter_rows_from_inputs


from scanner.persistence.repository import (
    repository_review_create,  # noqa: E402 - load after review row helpers
)

create_saved_review = repository_review_create.create_saved_review
_create_saved_review = repository_review_create._create_saved_review
_append_manual_review_filters = repository_review_create._append_manual_review_filters
_review_mcf_document_row = repository_review_create._review_mcf_document_row
_default_review_synchronization = repository_review_create._default_review_synchronization
_review_synchronization_row = repository_review_create._review_synchronization_row
_validate_synchronization_document = repository_review_create._validate_synchronization_document
_document_for_synchronization = repository_review_create._document_for_synchronization
_accepted_credits_signature = repository_review_create._accepted_credits_signature
_accepted_credits_row_for_save = repository_review_create._accepted_credits_row_for_save
_accepted_credits_snapshot = repository_review_create._accepted_credits_snapshot
_seed_review_mcf_document = repository_review_create._seed_review_mcf_document
_catalog_review_mcf_document = repository_review_create._catalog_review_mcf_document
_fill_mcf_document_gaps = repository_review_create._fill_mcf_document_gaps
_validate_review_mcf_runtime = repository_review_create._validate_review_mcf_runtime
_validate_review_synchronization_runtime = (
    repository_review_create._validate_review_synchronization_runtime
)
_with_manual_membership = repository_review_create._with_manual_membership
_AGGREGATE_FILTER_FIELDS = repository_review_create._AGGREGATE_FILTER_FIELDS
_EDL_FILTER_FIELDS = repository_review_create._EDL_FILTER_FIELDS
_MCF_FILTER_FIELDS = repository_review_create._MCF_FILTER_FIELDS
_MCF_DOCUMENT_FIELDS = repository_review_create._MCF_DOCUMENT_FIELDS


from scanner.persistence.repository import (
    repository_review_mutation,  # noqa: E402 - load after review validators
)

_revision_signatures = repository_review_mutation._revision_signatures
_check_review_revision = repository_review_mutation._check_review_revision
_claim_review_revision = repository_review_mutation._claim_review_revision
save_saved_review = repository_review_mutation.save_saved_review
retime_saved_review_draft = repository_review_mutation.retime_saved_review_draft
_review_retime_runtime = repository_review_mutation._review_retime_runtime
_validate_retime_filter_provenance = repository_review_mutation._validate_retime_filter_provenance
_map_retime_entries = repository_review_mutation._map_retime_entries
_retime_accepted_credits = repository_review_mutation._retime_accepted_credits
_save_saved_review = repository_review_mutation._save_saved_review
rename_saved_review = repository_review_mutation.rename_saved_review
duplicate_saved_review = repository_review_mutation.duplicate_saved_review
delete_saved_review = repository_review_mutation.delete_saved_review


from scanner.persistence.repository import (
    repository_review_operations,  # noqa: E402 - load after review mutations
)

get_scan_references = repository_review_operations.get_scan_references
render_saved_review_filter_preview = repository_review_operations.render_saved_review_filter_preview
render_credits_detection_preview = repository_review_operations.render_credits_detection_preview
_review_export_scans = repository_review_operations._review_export_scans
preflight_saved_review_mcf = repository_review_operations.preflight_saved_review_mcf
export_saved_review_mcf = repository_review_operations.export_saved_review_mcf
export_saved_review_edl = repository_review_operations.export_saved_review_edl


from scanner.persistence.repository import (
    repository_imports,  # noqa: E402 - load after review helpers exist
)

import_edl_as_saved_review = repository_imports.import_edl_as_saved_review
import_mcf_as_saved_review = repository_imports.import_mcf_as_saved_review
_import_mcf_as_saved_review = repository_imports._import_mcf_as_saved_review
_mcf_metadata_notices = repository_imports._mcf_metadata_notices


from scanner.persistence.repository import (
    repository_scan_lifecycle,  # noqa: E402 - load after review helpers exist
)

create_scan = repository_scan_lifecycle.create_scan
_validated_credits_acceptance = repository_scan_lifecycle._validated_credits_acceptance
accept_credits_detection = repository_scan_lifecycle.accept_credits_detection
get_credits_detection = repository_scan_lifecycle.get_credits_detection
find_active_credits_detection = repository_scan_lifecycle.find_active_credits_detection
delete_scan = repository_scan_lifecycle.delete_scan
