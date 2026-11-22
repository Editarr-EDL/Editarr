"""Validate document and filter fields required for strict MCF export."""

import math

from scanner.db import ReviewFilterRow
from scanner.mcf import MAX_LINE_CODEPOINTS
from scanner.modeling.model_core import channel_for_source
from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfPreflightIssue,
    McfWorkType,
    ReviewMcfDocument,
    Severity,
)


def _signature_value(value):
    return value.value if hasattr(value, "value") else value


def filter_mcf_value(review_filter, field):
    """Resolve implicit Review cue fields consistently for export and revision tracking."""
    value = getattr(review_filter, field)
    if value is None and field == "mcf_channel":
        return channel_for_source(review_filter.source)
    if value is None and field == "mcf_comment" and review_filter.details:
        # Each MCF payload occupies a single line. Keep the original review text intact.
        return " ".join(review_filter.details.splitlines()).strip() or None
    return value


def _mcf_document_issues(document, scans, *, durations=None) -> list[McfPreflightIssue]:
    """Check MCF document fields and timing against scan durations."""
    issues: list[McfPreflightIssue] = []
    metadata_fields = (
        ("title", "TITLE"),
        ("year", "YEAR"),
        ("work_type", "TYPE"),
        ("season", "SEASON"),
        ("episode", "EPISODE"),
        ("source_url", "SOURCE"),
        ("imdb_url", "IMDB"),
        ("release", "RELEASE"),
        ("comment", "COMMENT"),
    )
    for field, mcf_field in metadata_fields:
        value = getattr(document, field, None)
        if value is None:
            continue
        try:
            normalized = ReviewMcfDocument(**{field: value})
            normalized_value = getattr(normalized, field)
            if isinstance(normalized_value, McfWorkType):
                normalized_value = normalized_value.value
            if len(f"{mcf_field} {normalized_value}") > MAX_LINE_CODEPOINTS:
                raise ValueError("MCF metadata line is too long")
        except ValueError:
            issues.append(
                McfPreflightIssue(
                    code="invalid_mcf_metadata",
                    field=field,
                    message=f"MCF document field {field} is invalid",
                )
            )
    start = getattr(document, "material_start_seconds", None)
    end = getattr(document, "material_end_seconds", None)
    if (
        start is None
        or not isinstance(start, (int, float))
        or not math.isfinite(start)
        or start < 0
    ):
        issues.append(
            McfPreflightIssue(
                code="invalid_material_bounds",
                field="material_start_seconds",
                message="MCF material start is required and must be valid",
            )
        )
    known_durations = (
        durations
        if durations is not None
        else [scan.duration_seconds for scan in scans if scan.duration_seconds is not None]
    )
    if (
        end is None
        or not isinstance(end, (int, float))
        or not math.isfinite(end)
        or end <= 0
        or (isinstance(start, (int, float)) and end <= start)
        or (known_durations and end > min(known_durations))
    ):
        issues.append(
            McfPreflightIssue(
                code="invalid_material_bounds",
                field="material_end_seconds",
                message="MCF material end is required and must be valid",
            )
        )
    return issues


def _mcf_filter_issues(
    review_filter: ReviewFilterRow,
    position: int,
    duration_seconds: float | None = None,
) -> list[McfPreflightIssue]:
    """Check one selected filter for valid MCF classification and timing."""
    issues: list[McfPreflightIssue] = []
    identity = {"filter_id": review_filter.id, "filter_position": position}
    category = None
    if review_filter.category is None:
        issues.append(
            McfPreflightIssue(
                code="missing_category",
                field="category",
                message="Filter category is required",
                **identity,
            )
        )
    else:
        try:
            category = McfCategoryFamily(_signature_value(review_filter.category))
        except ValueError:
            issues.append(
                McfPreflightIssue(
                    code="invalid_category",
                    field="category",
                    message="Filter category is invalid",
                    **identity,
                )
            )
    subcategory = None
    if review_filter.subcategory is None:
        issues.append(
            McfPreflightIssue(
                code="missing_subcategory",
                field="subcategory",
                message="Filter subcategory is required",
                **identity,
            )
        )
    else:
        try:
            subcategory = McfCategory(review_filter.subcategory)
        except ValueError:
            issues.append(
                McfPreflightIssue(
                    code="invalid_subcategory",
                    field="subcategory",
                    message="Filter subcategory is invalid",
                    **identity,
                )
            )
    if (
        category is not None
        and subcategory is not None
        and MCF_CATEGORY_TO_FAMILY[subcategory.value] != category
    ):
        issues.append(
            McfPreflightIssue(
                code="invalid_category_pair",
                field="subcategory",
                message="Filter category and subcategory do not match",
                **identity,
            )
        )
    if review_filter.severity is None:
        issues.append(
            McfPreflightIssue(
                code="missing_severity",
                field="severity",
                message="Filter severity is required",
                **identity,
            )
        )
    else:
        try:
            Severity(_signature_value(review_filter.severity))
        except ValueError:
            issues.append(
                McfPreflightIssue(
                    code="invalid_severity",
                    field="severity",
                    message="Filter severity is invalid",
                    **identity,
                )
            )
    if review_filter.mcf_channel is not None:
        try:
            McfChannel(_signature_value(review_filter.mcf_channel))
        except ValueError:
            issues.append(
                McfPreflightIssue(
                    code="invalid_mcf_channel",
                    field="mcf_channel",
                    message="Filter MCF channel is invalid",
                    **identity,
                )
            )
    comment = filter_mcf_value(review_filter, "mcf_comment")
    if comment is not None and (
        not comment or "\n" in comment or "\r" in comment or "-->" in comment
    ):
        issues.append(
            McfPreflightIssue(
                code="invalid_mcf_comment",
                field="mcf_comment",
                message="Filter MCF comment is invalid",
                **identity,
            )
        )
    elif comment is not None:
        subcategory_value = review_filter.subcategory or ""
        severity_value = _signature_value(review_filter.severity) if review_filter.severity else ""
        channel_value = _signature_value(filter_mcf_value(review_filter, "mcf_channel"))
        if (
            len(f"{subcategory_value}={severity_value}={channel_value} # {comment}")
            > MAX_LINE_CODEPOINTS
        ):
            issues.append(
                McfPreflightIssue(
                    code="invalid_mcf_comment",
                    field="mcf_comment",
                    message="Filter MCF comment is invalid",
                    **identity,
                )
            )
    if (
        review_filter.end_seconds is None
        or not math.isfinite(review_filter.start_seconds)
        or not math.isfinite(review_filter.end_seconds)
        or review_filter.start_seconds < 0
        or review_filter.end_seconds <= review_filter.start_seconds
        or (duration_seconds is not None and review_filter.end_seconds > duration_seconds)
    ):
        issues.append(
            McfPreflightIssue(
                code="invalid_material_bounds",
                field="end_seconds",
                message="Filter time range is invalid",
                **identity,
            )
        )
    return issues
