"""Personal base defaults survive enforcement for manual and imported review content."""

from uuid import uuid4

import pytest

from scanner import repository
from scanner.db import (
    MediaItemRow,
    ScanRow,
    UserFilterCopyRuleRow,
    UserFilterCopySeverityRow,
    UserFilterPreferenceRow,
)
from scanner.formats.edl import parse_edl_content
from scanner.mcf import parse_mcf_content
from scanner.models import (
    FilterSelectionRule,
    McfCategory,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewMcfDocument,
)
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.filter_preference_rows import rule_columns
from tests.filter_policy_http_support import rule
from tests.mcf_import_support import _source_timing
from tests.multi_user_access_support import USER
from tests.test_review_policy_view import base_rows, enforce
from tests.test_review_policy_view import policy_review as policy_review


def independent_copy(accounts):
    with accounts.factory() as db:
        db.get_one(UserFilterPreferenceRow, USER).mode = "copy"
        db.add_all(
            UserFilterCopySeverityRow(
                user_id=USER, subcategory=category.value, position=position, severity="low"
            )
            for position, category in enumerate(McfCategory)
        )
        allow = FilterSelectionRule.model_validate(rule("allow", evidence_source="audio"))
        db.add(UserFilterCopyRuleRow(user_id=USER, id=allow.id, position=0, **rule_columns(allow)))
        db.commit()


@pytest.mark.parametrize("kind", ["manual", "edl", "mcf"])
def test_new_origin_uses_personal_base_and_current_owner_overlay(policy_review, monkeypatch, kind):
    accounts, _review_id = policy_review
    independent_copy(accounts)
    enforce(accounts, rules=[rule("deny", evidence_source="audio")], severities=["swearing"])
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 100)
    with accounts.factory() as db:
        db.info["review_actor"] = ActivityOwner(USER, 1)
        media = db.get(MediaItemRow, "media")
        media.path = db.get(ScanRow, "scan").resolved_local_path
        db.commit()
        if kind == "manual":
            detail = repository.create_saved_review(
                db,
                media_item_id=media.id,
                media_source_id=None,
                data=ReviewCreate(
                    name="Manual",
                    new_manual_filters=[
                        NewManualReviewFilter(
                            id=str(uuid4()),
                            source="audio",
                            category="language",
                            subcategory="swearing",
                            severity="low",
                            label="Manual evidence",
                            details=None,
                            group_key=None,
                            start_seconds=1,
                            end_seconds=2,
                            edit_action="mute",
                            selected=False,
                        )
                    ],
                ),
            )
        else:
            common = dict(
                db=db,
                media_item=media,
                resolved_local_path=media.path,
                media_source_id=None,
                source_name=f"input.{kind}",
                review_name="Imported",
                duration_seconds=100,
            )
            if kind == "edl":
                imported = repository.import_edl_as_saved_review(
                    **common, parsed=parse_edl_content(b"1 2 1\n")
                )
            else:
                parsed = parse_mcf_content(
                    b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\n"
                    b"START 00:00:00.000\nEND 00:01:40.000\n\n"
                    b"00:00:01.000 --> 00:00:02.000\nswearing=low=audio\n"
                )
                imported = repository.import_mcf_as_saved_review(
                    **common,
                    parsed=parsed,
                    catalog_mcf_document=ReviewMcfDocument(title="Movie"),
                    **_source_timing(parsed),
                )
            detail = imported.review
        review_id = detail.id
        assert detail.base_filters[0].selected is False
        assert detail.filters[0].selected is True
        if kind != "edl":
            assert detail.base_filters[0].severity.value == "low"
            assert detail.filters[0].severity.value == "high"
        else:
            assert detail.filters[0].subcategory is None
            assert not repository.preflight_saved_review_mcf(db, review_id).eligible
    before = base_rows(accounts, review_id)
    enforce(accounts, rules=[])
    with accounts.factory() as db:
        restored = repository.get_saved_review(db, review_id)
        assert restored.filters == restored.base_filters
        assert restored.filters[0].selected is False
    assert base_rows(accounts, review_id) == before
