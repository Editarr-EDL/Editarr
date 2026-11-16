from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    Base,
    MediaItemRow,
    ScanFindingRow,
)
from scanner.mcf import parse_mcf_content
from scanner.models import (
    EditAction,
    FilterDefaultsUpdate,
    FilterRuleDisposition,
    FilterSelectionRule,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    ReviewMcfDocument,
    Severity,
)
from tests.mcf_import_support import _parsed_document, _source_timing

FIXTURES = Path(__file__).parent / "fixtures" / "mcf"





@pytest.mark.parametrize(
    ("channels", "expected_audio", "expected_video", "expected_role"),
    [
        ([McfChannel.AUDIO], True, False, "audio"),
        ([McfChannel.VIDEO], False, True, "video"),
        ([McfChannel.BOTH], True, True, "both"),
        ([McfChannel.AUDIO, McfChannel.VIDEO], True, True, "both"),
        ([], True, True, "both"),
    ],
)
def test_mcf_import_derives_one_scan_capability_and_role_from_all_channels(
    channels, expected_audio, expected_video, expected_role
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Movie",
            name="Movie",
        )
        db.add(media)
        db.commit()
        parsed = _parsed_document(channels)

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path="/tmp/media.mkv",
            media_source_id=None,
            source_name="channels.mcf",
            review_name="Channels",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(material_end_seconds=10),
            duration_seconds=10,
            **_source_timing(parsed),
        )

        assert imported.scan.scan_audio is expected_audio
        assert imported.scan.scan_video is expected_video
        assert imported.review.memberships[0].role.value == expected_role
        assert len(imported.review.filters) == len(channels)
        assert [item.source for item in imported.review.filters] == [
            "audio" if channel == McfChannel.AUDIO else "visual"
            for channel in channels
        ]
        assert [item.edit_action for item in imported.review.filters] == [
            EditAction.MUTE if channel == McfChannel.AUDIO else EditAction.SKIP
            for channel in channels
        ]


def test_mcf_1_0_import_fills_review_metadata_but_preserves_source_document():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Movie",
            name="Catalog Movie",
        )
        db.add(media)
        db.commit()
        parsed = parse_mcf_content((FIXTURES / "valid-1.0.mcf").read_bytes())

        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path="/tmp/media.mkv",
            media_source_id=None,
            source_name="legacy.mcf",
            review_name="Legacy",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(
                title="Catalog Movie",
                year=2026,
                work_type="movie",
                material_end_seconds=6000,
            ),
            duration_seconds=6000,
            **_source_timing(parsed),
        )

        assert imported.scan.mcf_document.source_version == McfSourceVersion.V1_0_0
        assert imported.scan.mcf_document.title is None
        assert imported.review.mcf_document.title == "Catalog Movie"
        assert imported.review.mcf_document.year == 2026
        assert imported.review.mcf_document.material_start_seconds == 4.02
        assert imported.review.mcf_document.material_end_seconds == 5040.1
        assert imported.notices == []


def test_mcf_import_applies_selection_defaults_only_to_review_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaItemRow(
            id="media",
            server_id="server",
            jellyfin_id="item",
            item_type="Movie",
            name="Movie",
        )
        db.add(media)
        db.commit()
        settings = repository.get_structured_filter_defaults_settings(db)
        repository.update_filter_defaults_settings(
            db,
            FilterDefaultsUpdate(
                revision=settings.revision,
                selection_rules=[
                    FilterSelectionRule(
                        id=str(uuid4()),
                        disposition=FilterRuleDisposition.ALLOW,
                        subcategory=McfCategory.violence,
                        category=McfCategoryFamily.VIOLENCE,
                    )
                ],
                severity_defaults=settings.severity_defaults,
            ),
        )

        parsed = _parsed_document([McfChannel.VIDEO])
        imported = repository.import_mcf_as_saved_review(
            db,
            media_item=media,
            resolved_local_path="/tmp/media.mkv",
            media_source_id=None,
            source_name="defaults.mcf",
            review_name="Defaults",
            parsed=parsed,
            catalog_mcf_document=ReviewMcfDocument(material_end_seconds=10),
            duration_seconds=10,
            **_source_timing(parsed),
        )

        assert imported.review.filters[0].selected is False
        source = db.query(ScanFindingRow).one()
        assert source.selected is True
        assert source.severity == Severity.HIGH
        assert source.mcf_channel == McfChannel.VIDEO


