"""Pure update review for community master filter sets (local API §9)."""

import hashlib
import json

import pytest

from scanner.modeling.community_master_diff import (
    ACCEPT,
    CHANGED,
    DISMISS,
    KEEP_MINE,
    LOW_CONFIDENCE,
    NEW,
    REMOVED,
    BaselineRow,
    MasterFilter,
    MasterValues,
    allowed_decisions,
    can_accept,
    diff_master,
    master_filters,
)

BASE = MasterValues(600000, 605050, "punching", "high", "skip")
INFO = {"confidence": 0.39, "contributorCount": 4, "spread": {"startMs": 300, "endMs": 350}}


def _row(filter_id="f_a", values=BASE, touched=False):
    return BaselineRow(filter_id, "violence", values, touched)


def _served(filter_id="f_a", values=BASE, info=INFO, merged=()):
    return MasterFilter(filter_id, "violence", values, info, tuple(merged))


def _with(values=BASE, **changes):
    return MasterValues(**{**values.__dict__, **changes})


def test_dismissal_digest_is_sha256_of_compact_ordered_material_json():
    expected = (
        '{"startMs":600000,"endMs":605050,"subcategory":"punching","severity":"high",'
        '"action":"skip","lowConfidence":false,"present":true}'
    )
    assert BASE.digest() == hashlib.sha256(expected.encode()).hexdigest()


def test_unchanged_material_values_refresh_information_silently():
    info = {**INFO, "confidence": 0.8, "contributorCount": 9}
    diff = diff_master([_row()], [_served(info=info)])
    assert diff.rows == ()
    assert diff.info == {"f_a": info}


def test_filter_not_in_baseline_is_new_for_accept_or_dismiss():
    added = _served("f_b", _with(start_ms=1, end_ms=2))
    (row,) = diff_master([_row()], [_served(), added]).rows
    assert (row.change, row.filter_id, row.touched) == (NEW, "f_b", False)
    assert row.new_values == added.values
    assert allowed_decisions(NEW, False) == (ACCEPT, DISMISS)


@pytest.mark.parametrize("touched", [False, True])
def test_material_change_is_changed_with_its_fields(touched):
    newer = _with(start_ms=600100, severity="medium")
    (row,) = diff_master([_row(touched=touched)], [_served(values=newer)]).rows
    assert (row.change, row.changed_fields, row.touched) == (
        CHANGED,
        ("startMs", "severity"),
        touched,
    )
    expected = (KEEP_MINE, ACCEPT, DISMISS) if touched else (ACCEPT, DISMISS)
    assert allowed_decisions(CHANGED, touched) == expected


@pytest.mark.parametrize("touched", [False, True])
def test_absent_filter_is_removed_with_presence_false(touched):
    (row,) = diff_master([_row(touched=touched)], []).rows
    assert (row.change, row.changed_fields, row.new_values) == (
        REMOVED,
        ("present",),
        _with(present=False),
    )
    expected = (KEEP_MINE, ACCEPT, DISMISS) if touched else (ACCEPT, DISMISS)
    assert allowed_decisions(REMOVED, touched) == expected


def test_removed_row_already_accepted_as_absent_stays_quiet():
    assert diff_master([_row(values=_with(present=False))], []).rows == ()


def test_kept_row_that_returns_is_changed_by_presence():
    (row,) = diff_master([_row(values=_with(present=False), touched=True)], [_served()]).rows
    assert (row.change, row.changed_fields) == (CHANGED, ("present",))


@pytest.mark.parametrize("touched", [False, True])
def test_low_confidence_flip_alone_is_now_low_confidence(touched):
    (row,) = diff_master([_row(touched=touched)], [_served(values=_with(low_confidence=True))]).rows
    assert (row.change, row.changed_fields) == (LOW_CONFIDENCE, ("lowConfidence",))
    assert allowed_decisions(LOW_CONFIDENCE, touched) == (ACCEPT, DISMISS)


def test_low_confidence_flip_with_other_changes_is_changed():
    newer = _with(low_confidence=True, action="mute")
    (row,) = diff_master([_row()], [_served(values=newer)]).rows
    assert (row.change, row.changed_fields) == (CHANGED, ("action", "lowConfidence"))


def test_low_confidence_clearing_is_a_material_change():
    row = _row(values=_with(low_confidence=True))
    (update,) = diff_master([row], [_served()]).rows
    assert (update.change, update.changed_fields) == (CHANGED, ("lowConfidence",))


def test_dismissed_row_stays_hidden_until_master_values_change_again():
    newer = _served(values=_with(start_ms=600500))
    (row,) = diff_master([_row()], [newer]).rows
    dismissed = {"f_a": row.digest}

    again = diff_master([_row()], [newer], dismissed)
    assert again.rows == () and again.kept_dismissals == {"f_a"}

    changed_again = _served(values=_with(start_ms=600900))
    resurfaced = diff_master([_row()], [changed_again], dismissed)
    assert [update.new_values.start_ms for update in resurfaced.rows] == [600900]
    assert resurfaced.kept_dismissals == frozenset()


def test_dismissed_new_and_removed_rows_also_stay_hidden():
    added = _served("f_new", _with(start_ms=5, end_ms=9))
    first = diff_master([_row()], [added])
    dismissed = {row.filter_id: row.digest for row in first.rows}
    assert {row.change for row in first.rows} == {NEW, REMOVED}
    assert diff_master([_row()], [added], dismissed).rows == ()


def test_merged_ids_rekey_the_touched_row_and_remove_the_others():
    rows = [_row("f_1"), _row("f_2", touched=True), _row("f_3")]
    merged = _served("f_m", merged=("f_1", "f_2", "f_3"))
    diff = diff_master(rows, [merged])
    assert diff.rekeys == {"f_2": "f_m"}
    assert diff.info == {"f_m": INFO}
    assert [(row.filter_id, row.change, row.merged_into) for row in diff.rows] == [
        ("f_1", REMOVED, "f_m"),
        ("f_3", REMOVED, "f_m"),
    ]


def test_merged_ids_rekey_the_lowest_id_when_none_is_touched():
    diff = diff_master([_row("f_2"), _row("f_1")], [_served("f_m", merged=("f_2", "f_1"))])
    assert diff.rekeys == {"f_1": "f_m"}
    assert [(row.filter_id, row.merged_into) for row in diff.rows] == [("f_2", "f_m")]


def test_a_row_with_the_filters_own_id_continues_it():
    diff = diff_master([_row("f_a"), _row("f_b", touched=True)], [_served(merged=("f_b",))])
    assert diff.rekeys == {}
    assert [(row.filter_id, row.change, row.merged_into) for row in diff.rows] == [
        ("f_b", REMOVED, "f_a")
    ]


def test_rekeyed_row_is_compared_with_its_own_baseline():
    merged = _served("f_m", values=_with(end_ms=606000), merged=("f_1",))
    (row,) = diff_master([_row("f_1", touched=True)], [merged]).rows
    assert (row.filter_id, row.change, row.touched, row.changed_fields) == (
        "f_m",
        CHANGED,
        True,
        ("endMs",),
    )


def test_accept_needs_alignment_for_rows_with_bounds_on_fuzzy_links():
    for change in (NEW, CHANGED, LOW_CONFIDENCE):
        assert can_accept(change, fuzzy=False, aligned_for_revision=False)
        assert not can_accept(change, fuzzy=True, aligned_for_revision=False)
        assert can_accept(change, fuzzy=True, aligned_for_revision=True)
    assert can_accept(REMOVED, fuzzy=True, aligned_for_revision=False)


def test_master_filters_reads_snapshot_documents():
    document = json.loads(
        '{"filters":[{"filterId":"f_9","mergedIds":["f_1"],"category":"violence",'
        '"subcategory":"punching","severity":"high","action":"skip","startMs":1,"endMs":2,'
        '"spread":{"startMs":3,"endMs":4},"confidence":0.5,"contributorCount":2,'
        '"support":{"attestations":2,"upvotes":0},'
        '"negative":{"downvotes":0,"disputes":0,"coverageNegatives":0},"lowConfidence":true}]}'
    )
    (item,) = master_filters(document)
    assert item.filter_id == "f_9" and item.merged_ids == ("f_1",)
    assert item.values == MasterValues(1, 2, "punching", "high", "skip", True, True)
    assert set(item.info) == {"spread", "confidence", "contributorCount", "support", "negative"}
