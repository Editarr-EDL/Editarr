"""Pure snapshot reading and merged-ID re-keying for master filter sets (local API §8)."""

import json

from scanner.modeling.community_master_diff import (
    MasterFilter,
    MasterValues,
    master_filters,
    rekeys,
)

BASE = MasterValues(600000, 605050, "punching", "high", "skip")


def _served(filter_id="f_a", merged=()):
    return MasterFilter(filter_id, "violence", BASE, {}, tuple(merged))


def test_merged_ids_rekey_the_lowest_row_and_leave_the_others():
    assert rekeys(["f_2", "f_1", "f_3"], [_served("f_m", merged=("f_2", "f_1", "f_3"))]) == {
        "f_1": "f_m"
    }


def test_a_row_with_the_filters_own_id_continues_it():
    assert rekeys(["f_a", "f_b"], [_served(merged=("f_b",))]) == {}


def test_a_still_served_row_is_never_rekeyed():
    assert rekeys(["f_a", "f_b"], [_served("f_a"), _served("f_m", merged=("f_a",))]) == {}


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
