from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import api, repository
from scanner.db import Base, FilterSettingsRow, ScanFindingRow
from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    EditAction,
    FilterDefaultsSettings,
    FilterDefaultsUpdate,
    FilterRuleDisposition,
    FilterSelectionRule,
    McfCategory,
    McfChannel,
    McfSeverityDefault,
    NewManualReviewFilter,
    ReviewFilterInput,
    Severity,
)
from scanner.settings.filter_defaults import (
    apply_filter_defaults,
    fill_filter_defaults,
    fill_manual_filter_defaults,
)


def test_automatic_defaults_fill_missing_values_then_apply_all_rules():
    source = _filter(
        source="audio",
        subcategory=McfCategory.swearing,
        severity=None,
        channel=None,
        label="That BAD—word! again",
        confidence=0.01,
        selected=True,
    )
    settings = _settings(
        rules=[
            _rule(
                FilterRuleDisposition.DENY,
                evidence_source="audio",
                category=MCF_CATEGORY_TO_FAMILY["swearing"],
                subcategory=McfCategory.swearing,
                label_contains="bad word",
                severity=Severity.HIGH,
                mcf_channel=McfChannel.AUDIO,
            ),
            _rule(FilterRuleDisposition.ALLOW, label_contains="bad word"),
        ],
        severity_overrides={McfCategory.swearing: Severity.HIGH},
    )

    result = fill_filter_defaults([source], settings)

    assert result.filters[0].severity == Severity.HIGH
    assert result.filters[0].mcf_channel == McfChannel.AUDIO
    assert result.filters[0].selected is True
    assert result.model_dump(exclude={"filters"}) == {
        "selection_changes": 0,
        "severity_changes": 1,
        "channel_changes": 1,
    }
    assert source.severity is None
    assert source.mcf_channel is None
    assert result.filters[0] is not source


def test_allow_deselects_deny_wins_and_unmatched_selection_is_preserved():
    allow_only = _filter(label="fucking!", selected=True)
    deny_and_allow = _filter(label="fucking!", selected=False)
    unmatched = _filter(label="different", selected=False)
    settings = _settings(
        rules=[
            _rule(FilterRuleDisposition.DENY, label_contains="fucking"),
            _rule(FilterRuleDisposition.ALLOW, label_contains="fucking"),
        ]
    )

    allow_result = fill_filter_defaults(
        [allow_only],
        _settings(rules=[_rule(FilterRuleDisposition.ALLOW, label_contains="fucking")]),
    )
    result = fill_filter_defaults([deny_and_allow, unmatched], settings)

    assert allow_result.filters[0].selected is False
    assert allow_result.selection_changes == 1
    assert result.filters[0].selected is True
    assert result.filters[1].selected is False
    assert result.selection_changes == 1


def test_rule_clauses_are_anded_and_token_sequences_are_contiguous():
    source = _filter(source="visual", label="one noisy middle two")
    settings = _settings(
        rules=[
            _rule(
                FilterRuleDisposition.ALLOW,
                evidence_source="audio",
                label_contains="one",
            ),
            _rule(FilterRuleDisposition.ALLOW, label_contains="one two"),
        ]
    )

    result = fill_filter_defaults([source], settings)

    assert result.filters[0].selected is source.selected


def test_explicit_apply_overwrites_defaults_without_reclassifying_or_using_confidence():
    source = _filter(
        source="visual",
        subcategory=McfCategory.nudity,
        severity=Severity.LOW,
        channel=McfChannel.BOTH,
        confidence=1,
    )
    settings = _settings(severity_overrides={McfCategory.nudity: Severity.MEDIUM})

    result = apply_filter_defaults([source], settings)
    applied = result.filters[0]

    assert applied.severity == Severity.MEDIUM
    assert applied.mcf_channel == McfChannel.VIDEO
    assert applied.category == source.category
    assert applied.subcategory == source.subcategory
    assert applied.source == source.source
    assert applied.confidence == source.confidence
    assert result.severity_changes == 1
    assert result.channel_changes == 1
    assert source.severity == Severity.LOW
    assert source.mcf_channel == McfChannel.BOTH


def test_automatic_defaults_preserve_populated_severity_and_channel():
    source = _filter(
        source="visual",
        subcategory=McfCategory.nudity,
        severity=Severity.LOW,
        channel=McfChannel.BOTH,
    )
    settings = _settings(severity_overrides={McfCategory.nudity: Severity.HIGH})

    result = fill_filter_defaults([source], settings)

    assert result.filters[0].severity == Severity.LOW
    assert result.filters[0].mcf_channel == McfChannel.BOTH
    assert result.severity_changes == 0
    assert result.channel_changes == 0


def test_manual_filter_defaults_use_the_same_copy_and_rule_semantics():
    source = NewManualReviewFilter(
        id=str(uuid4()),
        source="visual",
        category=MCF_CATEGORY_TO_FAMILY["violence"],
        subcategory=McfCategory.violence,
        severity=None,
        mcf_channel=None,
        mcf_comment=None,
        label="intense scene",
        details=None,
        group_key=None,
        start_seconds=1,
        end_seconds=2,
        confidence=0.9,
        edit_action=EditAction.SKIP,
        selected=True,
    )
    settings = _settings(
        rules=[_rule(FilterRuleDisposition.ALLOW, label_contains="intense")],
        severity_overrides={McfCategory.violence: Severity.HIGH},
    )

    applied = fill_manual_filter_defaults([source], settings)[0]

    assert applied.severity == Severity.HIGH
    assert applied.mcf_channel == McfChannel.VIDEO
    assert applied.selected is False
    assert applied.category == source.category
    assert applied.subcategory == source.subcategory
    assert source.severity is None
    assert source.mcf_channel is None


def test_structured_settings_update_preserves_order_ids_and_canonicalizes_defaults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first_rule = _rule(FilterRuleDisposition.ALLOW, label_contains="  bad word  ")
        second_rule = _rule(
            FilterRuleDisposition.DENY,
            category=MCF_CATEGORY_TO_FAMILY["violence"],
        )
        source = _settings(
            rules=[second_rule, first_rule],
            severity_overrides={McfCategory.swearing: Severity.HIGH},
        )
        reversed_defaults = list(reversed(source.severity_defaults))

        updated = repository.update_filter_defaults_settings(
            db,
            FilterDefaultsUpdate(
                revision=0,
                selection_rules=source.selection_rules,
                severity_defaults=reversed_defaults,
            ),
        )

        assert updated.revision == 1
        assert [rule.id for rule in updated.selection_rules] == [
            second_rule.id,
            first_rule.id,
        ]
        assert updated.selection_rules[1].label_contains == "bad word"
        assert updated.severity_defaults[0].subcategory == McfCategory.commercial
        assert updated.severity_defaults[-1].subcategory == McfCategory.weapons
        assert (
            next(
                item
                for item in updated.severity_defaults
                if item.subcategory == McfCategory.swearing
            ).severity
            == Severity.HIGH
        )


def test_structured_settings_noop_and_stale_update_make_no_writes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        original = repository.get_structured_filter_defaults_settings(db)
        row = db.get(FilterSettingsRow, 1)
        original_updated_at = row.updated_at

        no_op = repository.update_filter_defaults_settings(
            db, FilterDefaultsUpdate(**original.model_dump())
        )

        assert no_op.revision == 0
        assert db.get(FilterSettingsRow, 1).updated_at == original_updated_at
        with pytest.raises(repository.FilterSettingsRevisionConflict):
            repository.update_filter_defaults_settings(
                db,
                FilterDefaultsUpdate(
                    revision=1,
                    selection_rules=[_rule(FilterRuleDisposition.ALLOW, label_contains="changed")],
                    severity_defaults=original.severity_defaults,
                ),
            )
        assert repository.get_structured_filter_defaults_settings(db) == original
        assert db.query(ScanFindingRow).count() == 0


def test_concurrent_settings_writes_cannot_overwrite_a_committed_revision(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'settings.db'}")
    Base.metadata.create_all(engine)
    first = Session(engine)
    second = Session(engine)
    try:
        first_settings = repository.get_structured_filter_defaults_settings(first)
        second_settings = repository.get_structured_filter_defaults_settings(second)
        first_rule = _rule(FilterRuleDisposition.ALLOW, label_contains="first")
        second_rule = _rule(FilterRuleDisposition.ALLOW, label_contains="second")

        committed = repository.update_filter_defaults_settings(
            first,
            FilterDefaultsUpdate(
                revision=first_settings.revision,
                selection_rules=[first_rule],
                severity_defaults=first_settings.severity_defaults,
            ),
        )
        with pytest.raises(repository.FilterSettingsRevisionConflict):
            repository.update_filter_defaults_settings(
                second,
                FilterDefaultsUpdate(**second_settings.model_dump()),
            )
        with pytest.raises(repository.FilterSettingsRevisionConflict):
            repository.update_filter_defaults_settings(
                second,
                FilterDefaultsUpdate(
                    revision=second_settings.revision,
                    selection_rules=[second_rule],
                    severity_defaults=second_settings.severity_defaults,
                ),
            )

        second.expire_all()
        persisted = repository.get_structured_filter_defaults_settings(second)
        assert committed.revision == 1
        assert persisted.revision == 1
        assert [rule.id for rule in persisted.selection_rules] == [first_rule.id]
    finally:
        first.close()
        second.close()
        engine.dispose()


def test_rule_normalization_rejects_wildcards_duplicates_and_invalid_pairs():
    with pytest.raises(ValueError, match="at least one match clause"):
        _rule(FilterRuleDisposition.ALLOW, label_contains="  ")
    with pytest.raises(ValueError, match="include its category"):
        _rule(FilterRuleDisposition.ALLOW, subcategory=McfCategory.swearing)
    with pytest.raises(ValueError, match="does not belong"):
        _rule(
            FilterRuleDisposition.ALLOW,
            category=MCF_CATEGORY_TO_FAMILY["nudity"],
            subcategory=McfCategory.swearing,
        )

    duplicate = _rule(FilterRuleDisposition.ALLOW, label_contains="duplicate")
    with pytest.raises(ValueError, match="IDs must be unique"):
        _settings(rules=[duplicate, duplicate])


def test_filter_defaults_openapi_uses_structured_contracts():
    schema = api.app.openapi()

    settings_path = schema["paths"]["/api/settings/filters"]
    assert settings_path["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/FilterDefaultsSettings")
    assert settings_path["put"]["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/InstanceDefaultsUpdate")
    assert settings_path["put"]["responses"]["409"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ReviewError")

    apply_operation = schema["paths"]["/api/settings/filters/apply"]["post"]
    assert apply_operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/FilterDefaultsApplyRequest"
    )
    assert apply_operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ReviewFilterDefaultsApplyResult")


def _settings(*, rules=(), severity_overrides=None):
    overrides = severity_overrides or {}
    return FilterDefaultsSettings(
        revision=0,
        selection_rules=list(rules),
        severity_defaults=[
            McfSeverityDefault(
                subcategory=subcategory,
                severity=overrides.get(subcategory, Severity.MEDIUM),
            )
            for subcategory in McfCategory
        ],
    )


def _rule(disposition, **clauses):
    return FilterSelectionRule(
        id=str(uuid4()),
        disposition=disposition,
        **clauses,
    )


def _filter(
    *,
    source="audio",
    subcategory=McfCategory.swearing,
    severity=Severity.MEDIUM,
    channel=McfChannel.AUDIO,
    label="label",
    confidence=0.5,
    selected=True,
):
    return ReviewFilterInput(
        id=str(uuid4()),
        source_scan_id="scan-1",
        source_finding_id=1,
        source=source,
        category=MCF_CATEGORY_TO_FAMILY[subcategory.value],
        subcategory=subcategory,
        severity=severity,
        mcf_channel=channel,
        mcf_comment=None,
        label=label,
        details=None,
        group_key=None,
        start_seconds=1,
        end_seconds=2,
        confidence=confidence,
        edit_action=EditAction.MUTE if source == "audio" else EditAction.SKIP,
        selected=selected,
    )
