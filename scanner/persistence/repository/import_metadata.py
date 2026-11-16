"""Metadata preparation for imported evidence and editable review copies."""

from scanner import repository as root
from scanner.db import MediaItemRow
from scanner.models import (
    McfMetadataNotice,
    McfWorkType,
    ReviewMcfDocument,
    ReviewSynchronization,
    ScanMcfDocument,
)


def prepare_edl_review_document(
    media_item: MediaItemRow,
    catalog: ReviewMcfDocument | None,
    synchronization: ReviewSynchronization | None,
) -> ReviewMcfDocument:
    """Prepare catalog metadata for EDL without inventing a canonical material end."""
    document = catalog or ReviewMcfDocument(
        title=media_item.name,
        work_type={
            "movie": McfWorkType.MOVIE,
            "episode": McfWorkType.EPISODE,
            "series": McfWorkType.SERIES,
        }.get(media_item.item_type.casefold()),
        material_start_seconds=0,
    )
    document = document.model_copy(update={"material_end_seconds": None})
    return root._document_for_synchronization(document, synchronization)


def prepare_mcf_review_documents(
    source: ScanMcfDocument,
    catalog: ReviewMcfDocument,
    synchronization: ReviewSynchronization,
    material_end_seconds: float | None,
) -> tuple[ReviewMcfDocument, ReviewMcfDocument]:
    """Keep source metadata separate from a gap-filled, synchronized review copy."""
    imported = ReviewMcfDocument(**source.model_dump(exclude={"source_version"}))
    review = root._fill_mcf_document_gaps(imported, catalog)
    review = review.model_copy(
        update={
            "material_start_seconds": synchronization.start_seconds,
            "material_end_seconds": material_end_seconds,
        }
    )
    root._validate_synchronization_document(synchronization, review)
    return imported, review


def _mcf_metadata_notices(
    imported: ReviewMcfDocument, catalog: ReviewMcfDocument
) -> list[McfMetadataNotice]:
    """Describe MCF metadata values that differ from the selected catalog source."""
    fields = [
        field
        for field in (
            "title",
            "year",
            "work_type",
            "season",
            "episode",
            "source_url",
            "imdb_url",
            "release",
            "comment",
        )
        if getattr(imported, field) is not None
        and getattr(catalog, field) is not None
        and getattr(imported, field) != getattr(catalog, field)
    ]
    if not fields:
        return []
    return [
        McfMetadataNotice(
            code="metadata_mismatch",
            fields=fields,
            mcf_metadata=imported,
            catalog_metadata=catalog,
        )
    ]
