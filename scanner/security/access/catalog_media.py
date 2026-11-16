"""Capability rows for the matrix's media, scans, and reviews section."""

from __future__ import annotations

from .capability_ids import Capability as C
from .capability_spec import CapabilitySpec, spec
from .vocabulary import ADMINS, EVERYONE, Target

_OWN = Target.OWN_RESOURCE
_OTHER = Target.OTHER_RESOURCE

_BROWSE = "Browse authorized Jellyfin media and artwork"
_FINDINGS = "Read/reuse completed scan findings for authorized media"
_START = "Start audio/video/combined scans, rerun, choose tracks/models/options"
_PATHS = "See underlying filesystem paths"
_IDENTITY = "View/edit manual media identity/source associations"
_OWN_JOBS = "View own job progress/errors"
_OTHER_JOBS = "View others' queue/progress/errors"
_CANCEL = "Cancel/resume queued/interrupted jobs"
_REORDER = "Reorder global queue"
_SCAN_NAMES = "Rename/delete completed scans"
_OWN_REVIEWS = "Create/save/rename/duplicate/delete reviews"
_OTHER_REVIEWS = "View/copy/edit/delete/export others' reviews"
_EVIDENCE = "Attach/remove scan evidence; edit filters/selection/classification/severity"
_CUES = "Edit actions/channels/comments/review and MCF metadata"
_TIMING = "Synchronize/retime; detect/accept/edit/remove credits"
_PREVIEWS = "Play audio/mute/video previews"
_UPLOAD = "Upload EDL/MCF to reviews"
_EXPORT_OTHER = "Export another user's review to self or review owner"

MEDIA_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    spec(C.MEDIA_BROWSE, Target.MEDIA, EVERYONE, _BROWSE),
    spec(C.SCANS_FINDINGS_READ, Target.MEDIA, EVERYONE, _FINDINGS),
    spec(C.SCANS_START, Target.MEDIA, EVERYONE, _START),
    spec(C.MEDIA_PATHS_VIEW, Target.MEDIA, ADMINS, _PATHS),
    spec(C.MEDIA_IDENTITY_EDIT, Target.MEDIA, EVERYONE, _IDENTITY),
    spec(C.SCAN_JOBS_VIEW_OWN, _OWN, EVERYONE, _OWN_JOBS),
    spec(C.SCAN_JOBS_VIEW_OTHER, _OTHER, ADMINS, _OTHER_JOBS),
    spec(C.SCAN_JOBS_CANCEL_OWN, _OWN, EVERYONE, _CANCEL),
    spec(C.SCAN_JOBS_CANCEL_OTHER, _OTHER, ADMINS, _CANCEL),
    spec(C.SCAN_JOBS_RESUME_OWN, _OWN, EVERYONE, _CANCEL),
    spec(C.SCAN_JOBS_RESUME_OTHER, _OTHER, ADMINS, _CANCEL),
    spec(C.SCAN_JOBS_REORDER, Target.INSTANCE, ADMINS, _REORDER),
    spec(C.SCANS_RENAME_OWN, _OWN, EVERYONE, _SCAN_NAMES),
    spec(C.SCANS_RENAME_OTHER, _OTHER, ADMINS, _SCAN_NAMES),
    spec(C.SCANS_DELETE_OWN, _OWN, EVERYONE, _SCAN_NAMES),
    spec(C.SCANS_DELETE_OTHER, _OTHER, ADMINS, _SCAN_NAMES),
    spec(C.REVIEWS_CREATE_OWN, _OWN, EVERYONE, _OWN_REVIEWS),
    spec(C.REVIEWS_VIEW_OWN, _OWN, EVERYONE, _OWN_REVIEWS),
    spec(C.REVIEWS_SAVE_OWN, _OWN, EVERYONE, _OWN_REVIEWS),
    spec(C.REVIEWS_RENAME_OWN, _OWN, EVERYONE, _OWN_REVIEWS),
    spec(C.REVIEWS_DUPLICATE_OWN, _OWN, EVERYONE, _OWN_REVIEWS),
    spec(C.REVIEWS_DELETE_OWN, _OWN, EVERYONE, _OWN_REVIEWS),
    spec(C.REVIEWS_VIEW_OTHER, _OTHER, ADMINS, _OTHER_REVIEWS),
    spec(C.REVIEWS_COPY_OTHER, _OTHER, ADMINS, _OTHER_REVIEWS),
    spec(C.REVIEWS_EDIT_OTHER, _OTHER, ADMINS, _OTHER_REVIEWS),
    spec(C.REVIEWS_DELETE_OTHER, _OTHER, ADMINS, _OTHER_REVIEWS),
    spec(C.REVIEWS_EXPORT_OTHER, _OTHER, ADMINS, _OTHER_REVIEWS, _EXPORT_OTHER),
    spec(C.REVIEWS_EVIDENCE_EDIT_OWN, _OWN, EVERYONE, _EVIDENCE),
    spec(C.REVIEWS_EVIDENCE_EDIT_OTHER, _OTHER, ADMINS, _EVIDENCE),
    spec(C.REVIEWS_FILTERS_EDIT_OWN, _OWN, EVERYONE, _EVIDENCE),
    spec(C.REVIEWS_FILTERS_EDIT_OTHER, _OTHER, ADMINS, _EVIDENCE),
    spec(C.REVIEWS_CUES_EDIT_OWN, _OWN, EVERYONE, _CUES),
    spec(C.REVIEWS_CUES_EDIT_OTHER, _OTHER, ADMINS, _CUES),
    spec(C.REVIEWS_METADATA_EDIT_OWN, _OWN, EVERYONE, _CUES),
    spec(C.REVIEWS_METADATA_EDIT_OTHER, _OTHER, ADMINS, _CUES),
    spec(C.REVIEWS_TIMING_EDIT_OWN, _OWN, EVERYONE, _TIMING),
    spec(C.REVIEWS_TIMING_EDIT_OTHER, _OTHER, ADMINS, _TIMING),
    spec(C.REVIEWS_CREDITS_EDIT_OWN, _OWN, EVERYONE, _TIMING),
    spec(C.REVIEWS_CREDITS_EDIT_OTHER, _OTHER, ADMINS, _TIMING),
    spec(C.REVIEWS_PREVIEW_OWN, _OWN, EVERYONE, _PREVIEWS),
    spec(C.REVIEWS_PREVIEW_OTHER, _OTHER, ADMINS, _PREVIEWS),
    spec(C.REVIEWS_IMPORT_UPLOAD, _OWN, EVERYONE, _UPLOAD),
)
