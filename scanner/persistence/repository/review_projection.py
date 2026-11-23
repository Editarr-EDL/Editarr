"""Path-free projections of already authorized saved-review results."""

from collections.abc import Iterator

from pydantic import BaseModel


def verification_source_ids(value) -> Iterator[str]:
    """Find original scan IDs in a review response before applying its projection."""
    if isinstance(value, BaseModel):
        value = value.__dict__
    if isinstance(value, list):
        for item in value:
            yield from verification_source_ids(item)
    elif isinstance(value, dict):
        source = value.get("verified_from_scan_id")
        if isinstance(source, str) and source:
            yield source
        for item in value.values():
            yield from verification_source_ids(item)


def project_review(value, *, paths=False, visible=None, scan_visible=None):
    if isinstance(value, list):
        return [
            project_review(item, paths=paths, visible=visible, scan_visible=scan_visible)
            for item in value
        ]
    if isinstance(value, BaseModel):
        return type(value).model_validate(
            project_review(
                value.model_dump(mode="python"),
                paths=paths,
                visible=visible,
                scan_visible=scan_visible,
            )
        )
    if not isinstance(value, dict):
        return value
    result = {}
    hidden_export = (
        visible is not None
        and "exported_format_revision" in value
        and value.get("review_id") is not None
        and not visible(value["review_id"])
    )
    for key, item in value.items():
        if hidden_export and key in {"review_id", "exported_format_revision", "exported_review"}:
            result[key] = None
        elif key == "verified_from_scan_id" and item and scan_visible and not scan_visible(item):
            result[key] = None
        elif key == "verification_coverage":
            result[key] = None
        elif not paths and key in {"path", "source_path", "resolved_local_path"}:
            result[key] = None
        elif not paths and key in {"error_message", "progress_message"} and item:
            result[key] = None
        else:
            result[key] = project_review(
                item,
                paths=paths,
                visible=visible,
                scan_visible=scan_visible,
            )
    return result
