"""Catalog scan listings, path mapping, and capability routes."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.api import PathResolutionRequest, _review_error_responses
from scanner.db import get_db
from scanner.integrations.jellyfin import JellyfinError
from scanner.models import (
    MCF_CATEGORY_FAMILY_LABELS,
    MCF_CATEGORY_REGISTRY,
    ExportFormat,
    ExportStatus,
    McfRegistry,
    PathMapping,
    PathMappingCreate,
    PathMappingReorder,
    PathMappingUpdate,
    PathResolution,
    Scan,
    ScanCapabilities,
    ScanModelCapability,
)
from scanner.persistence.repository.export_discovery import catalog_publication_status
from scanner.repository import (
    create_path_mapping,
    delete_library_item_export,
    delete_path_mapping,
    list_path_mappings,
    list_scans_for_library_item,
    media_item_export_status,
    reorder_path_mappings,
    resolve_local_path,
    update_path_mapping,
)
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.request_access import request_access
from scanner.security.audit.actions import AuditAction
from scanner.settings.capabilities import (
    QWEN_MODEL_DISPLAY_NAMES,
    RECOMMENDED_QWEN_MODEL,
    SUPPORTED_AUDIO_FILTERS,
    SUPPORTED_AUDIO_MODELS,
    SUPPORTED_QWEN_MODELS,
    SUPPORTED_VIDEO_MODELS,
    current_environment,
)

from .catalog_access import CatalogViewer, catalog_viewer
from .catalog_artwork import artwork_headers
from .catalog_library_api import authorized_jellyfin_item
from .operations_access import Operator, operation
from .review_access import review_caller
from .scan_access import ScanCaller, scan_caller

router = APIRouter()
_MAPPING = AuditAction.PATH_MAPPING_CHANGE


@router.get("/api/library-items/{item_id}/scans", response_model=list[Scan])
def api_list_library_item_scans(
    item_id: str,
    db: Session = Depends(get_db),
    caller: ScanCaller = Depends(scan_caller),
):
    # Scan history is owned locally and stays available even if Jellyfin is
    # temporarily unavailable or its connection settings have been replaced.
    """List scans associated with a Jellyfin item."""
    return caller.visible(list_scans_for_library_item(db, jellyfin_item_id=item_id))


@router.get(
    "/api/library-items/{item_id}/exports",
    response_model=ExportStatus,
    responses=_review_error_responses(409),
)
def api_library_item_exports(
    item_id: str,
    media_source_id: str | None = None,
    db: Session = Depends(get_db),
    caller=Depends(review_caller),
    viewer: CatalogViewer = Depends(catalog_viewer(C.MEDIA_BROWSE)),
):
    """Read managed export statuses for a Jellyfin media version."""
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise root._review_http_error(
            "media_unavailable", "Configure Jellyfin before loading exports"
        )
    try:
        item, _library = authorized_jellyfin_item(viewer, credentials, item_id)
    except JellyfinError as exc:
        raise HTTPException(502, str(exc)) from exc
    status = media_item_export_status(
        db,
        server_id=credentials[0],
        jellyfin_item_id=item_id,
        media_source_id=media_source_id or "",
    )
    return caller.present(
        catalog_publication_status(
            caller,
            status,
            server_id=credentials[0],
            item_id=item_id,
            item_type=item.item_type,
            media_source_id=media_source_id or "",
            required=True,
        )
    )


@router.delete(
    "/api/library-items/{item_id}/exports/{format}",
    status_code=204,
    responses=_review_error_responses(404, 409, 500),
)
def api_delete_library_item_export(
    item_id: str,
    format: ExportFormat,
    media_source_id: str | None = None,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(catalog_viewer(C.EXPORTS_DEFAULT_DELETE)),
):
    """Delete the legacy adjacent sidecar of a Jellyfin media version the caller may reach."""
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise root._review_http_error(
            "media_unavailable", "Configure Jellyfin before deleting an export"
        )
    try:
        authorized_jellyfin_item(viewer, credentials, item_id)
    except JellyfinError as exc:
        raise HTTPException(502, str(exc)) from exc
    try:
        deleted = delete_library_item_export(
            db,
            server_id=credentials[0],
            jellyfin_item_id=item_id,
            media_source_id=media_source_id,
            export_format=format,
        )
    except ValueError as exc:
        raise root._review_http_error("export_delete_failed", str(exc), 500) from exc
    if not deleted:
        raise root._review_http_error("export_not_found", "Export not found", 404)
    return Response(status_code=204)


@router.get("/api/library-items/{item_id}/image")
def api_library_item_primary_image(
    item_id: str,
    tag: str | None = None,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(catalog_viewer(C.MEDIA_BROWSE)),
):
    """Serve Jellyfin artwork for an item the caller may reach."""
    credentials = root.get_jellyfin_credentials(db)
    if not credentials:
        raise HTTPException(409, "Configure Jellyfin before loading artwork")
    try:
        if not viewer.is_owner:
            authorized_jellyfin_item(viewer, credentials, item_id)
        with root.JellyfinClient(*credentials) as client:
            image = client.primary_image(item_id, tag)
    except JellyfinError as exc:
        root.record_jellyfin_connection(db, str(exc))
        raise HTTPException(502, str(exc)) from exc
    if image is None:
        raise HTTPException(404, "No primary image found")
    root.record_jellyfin_connection(db, None)
    content, media_type = image
    return Response(
        content=content,
        media_type=media_type,
        headers=artwork_headers(viewer, content),
    )


@router.get(
    "/api/settings/path-mappings",
    response_model=list[PathMapping],
    dependencies=[Depends(operation(C.PATH_MAPPINGS_VIEW, paths=True))],
)
def api_list_path_mappings(db: Session = Depends(get_db)):
    return list_path_mappings(db)


@router.post("/api/settings/path-mappings", response_model=PathMapping)
def api_create_path_mapping(
    data: PathMappingCreate,
    db: Session = Depends(get_db),
    caller: Operator = Depends(operation(C.PATH_MAPPINGS_CREATE, _MAPPING, paths=True)),
):
    caller.stage(_MAPPING, {"change": "created"})
    try:
        return create_path_mapping(db, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/api/settings/path-mappings/{mapping_id}", response_model=PathMapping)
def api_update_path_mapping(
    mapping_id: int,
    data: PathMappingUpdate,
    db: Session = Depends(get_db),
    caller: Operator = Depends(operation(C.PATH_MAPPINGS_UPDATE, _MAPPING, paths=True)),
):
    caller.stage(_MAPPING, {"change": "updated"}, resource=str(mapping_id))
    try:
        mapping = update_path_mapping(db, mapping_id, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not mapping:
        raise HTTPException(404, "Path mapping not found")
    return mapping


@router.delete("/api/settings/path-mappings/{mapping_id}")
def api_delete_path_mapping(
    mapping_id: int,
    db: Session = Depends(get_db),
    caller: Operator = Depends(operation(C.PATH_MAPPINGS_DELETE, _MAPPING)),
):
    caller.stage(_MAPPING, {"change": "deleted"}, resource=str(mapping_id))
    if not delete_path_mapping(db, mapping_id):
        raise HTTPException(404, "Path mapping not found")
    return {"deleted": True}


@router.post("/api/settings/path-mappings/reorder", response_model=list[PathMapping])
def api_reorder_path_mappings(
    data: PathMappingReorder,
    db: Session = Depends(get_db),
    caller: Operator = Depends(operation(C.PATH_MAPPINGS_REORDER, _MAPPING, paths=True)),
):
    caller.stage(_MAPPING, {"change": "reordered"})
    try:
        return reorder_path_mappings(db, data.mapping_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/api/settings/path-mappings/resolve",
    response_model=PathResolution,
    dependencies=[Depends(operation(C.PATH_MAPPINGS_TEST, paths=True))],
)
def api_resolve_path_mapping(data: PathResolutionRequest, db: Session = Depends(get_db)):
    try:
        return resolve_local_path(db, data.remote_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get(
    "/api/capabilities",
    response_model=ScanCapabilities,
    dependencies=[Depends(catalog_viewer(C.SCANS_START))],
)
def api_capabilities():
    """Advertise backend-supported models so every client renders the same choices."""
    environment = current_environment()
    capabilities = root.model_capabilities(environment)
    by_id = {capability.id: capability for capability in capabilities}

    def choices(ids: tuple[str, ...]) -> list[ScanModelCapability]:
        return [
            ScanModelCapability(
                id=backend_id,
                display_name=by_id[backend_id].display_name,
                recommended=by_id[backend_id].recommended,
            )
            for backend_id in ids
            if by_id[backend_id].availability == "available"
        ]

    return {
        "audio_models": choices(SUPPORTED_AUDIO_MODELS),
        "audio_filters": choices(SUPPORTED_AUDIO_FILTERS),
        "video_models": choices(SUPPORTED_VIDEO_MODELS),
        "qwen_models": [
            ScanModelCapability(
                id=model_id,
                display_name=QWEN_MODEL_DISPLAY_NAMES[model_id],
                recommended=model_id == RECOMMENDED_QWEN_MODEL,
            )
            for model_id in SUPPORTED_QWEN_MODELS
        ],
        "target_id": environment.target_id,
        "model_capabilities": list(capabilities),
    }


@router.get("/api/mcf-registry", response_model=McfRegistry, dependencies=[Depends(request_access)])
def api_mcf_registry():
    """Return the supported MCF families, categories, and labels."""
    return McfRegistry(
        families=[
            {
                "token": family,
                "label": MCF_CATEGORY_FAMILY_LABELS[family],
                "categories": [{"token": token, "label": label} for token, label in categories],
            }
            for family, categories in MCF_CATEGORY_REGISTRY.items()
        ]
    )
