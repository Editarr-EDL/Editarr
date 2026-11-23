"""Register saved-review routes and preserve their public entry points."""

from fastapi import APIRouter

from . import (
    review_deletion_api,
    review_edit_api,
    review_export_api,
    review_repair_api,
    review_subtitle_api,
    review_target_api,
    review_target_routes,
)

router = APIRouter()
router.include_router(review_target_routes.router)
router.include_router(review_edit_api.router)
router.include_router(review_export_api.router)
router.include_router(review_subtitle_api.router)
router.include_router(review_deletion_api.router)
router.include_router(review_repair_api.router)

_review_http_error = review_target_api._review_http_error
_review_mutation_error = review_target_api._review_mutation_error
_catalog_review_media = review_target_api._catalog_review_media
_mcf_work_type = review_target_api._mcf_work_type
_list_catalog_reviews = review_target_api._list_catalog_reviews
_refresh_saved_review_target = review_target_api._refresh_saved_review_target
api_list_jellyfin_reviews = review_target_routes.api_list_jellyfin_reviews
api_create_jellyfin_review = review_target_routes.api_create_jellyfin_review

api_get_saved_review = review_edit_api.api_get_saved_review
api_save_saved_review = review_edit_api.api_save_saved_review
api_retime_saved_review = review_edit_api.api_retime_saved_review
api_rename_saved_review = review_edit_api.api_rename_saved_review
api_duplicate_saved_review = review_edit_api.api_duplicate_saved_review
api_delete_saved_review = review_edit_api.api_delete_saved_review
api_saved_review_mute_preview = review_edit_api.api_saved_review_mute_preview
api_saved_review_video_preview = review_edit_api.api_saved_review_video_preview
api_export_saved_review = review_edit_api.api_export_saved_review
api_preflight_saved_review_mcf = review_edit_api.api_preflight_saved_review_mcf
api_export_saved_review_mcf = review_edit_api.api_export_saved_review_mcf
