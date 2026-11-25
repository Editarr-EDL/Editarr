"""What counts as a user's edit of a community review filter, owner policy, refresh of
filters outside the file, and Locally verified from the filter's own bounds (ADR 0002
U2–U7)."""

import scanner.api  # noqa: F401 - load the application before repository modules
from scanner.db import ReviewSynchronizationRow, SavedReviewRow, ScanFindingRow, ScanRow
from scanner.persistence.repository.repository_review_core import get_saved_review
from scanner.persistence.repository.review_policy import prepare_review_policy
from tests.community_master_service_support import served_filter
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.test_review_community_sources import (
    activities,
    community,
    include,
    keep,
    masters,
    review,
    save,
    session,
)
from tests.test_review_policy_view import enforce

__all__ = ["activities", "masters", "review"]


def _publish(masters, link, revision, *filters):
    masters.service.publish("v_1", revision, *filters)
    masters.ok("POST", f"/masters/{link['linkId']}/check", USER, {})


def test_owner_policy_applies_to_community_filters_like_any_source(masters, review):
    link = masters.link(USER, "v_1", "2", served_filter("f_weak", 10_000, 20_000, confidence=0.2))
    masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 0.5})
    saved = save(masters, review, include(link["linkId"]))
    filter_id = community(saved)["f_weak"].id
    assert not community(saved)["f_weak"].selected
    enforce(masters.app.accounts, rules=[rule("deny", category="violence")])
    with session(masters) as db:
        prepared = prepare_review_policy(db, db.get(SavedReviewRow, review))
        effective = {value.id: value for value in prepared.filters}
        assert effective[filter_id].selected and filter_id in prepared.required_ids
        assert not {value.id: value for value in prepared.base_filters}[filter_id].selected


def test_only_direct_edits_mark_a_community_filter_edited(masters, review):
    link = masters.link(
        USER, "v_1", "2", served_filter("f_a"), served_filter("f_b", 40_000, 41_000)
    )
    saved = save(masters, review, include(link["linkId"]))

    def bulk(filters):
        # Apply Filter Defaults changes values without the user-edit flag.
        for value in filters:
            if value["community_filter_id"] == "f_a":
                value.update(selected=False, severity="low")

    defaulted = save(masters, review, keep(saved), edit=bulk)
    assert not community(defaulted)["f_a"].community_edited
    assert community(defaulted)["f_a"].severity == "low"

    def unset(filters):
        for value in filters:
            if value["community_filter_id"] == "f_b":
                value.update(selected=False, community_edited=True)

    edited = save(masters, review, keep(defaulted), edit=unset)
    cleared = save(
        masters,
        review,
        keep(edited),
        edit=lambda filters: [{**value, "community_edited": False} for value in filters],
    )
    assert community(cleared)["f_b"].community_edited, "a user edit never resets"
    _publish(
        masters, link, 2, served_filter("f_a", 11_000, 20_000), served_filter("f_b", 40_000, 41_000)
    )
    refreshed = save(masters, review, keep(cleared, refresh=True))
    # The bulk change wasn't the user's, so the filter takes the new master values.
    assert (community(refreshed)["f_a"].start_seconds, community(refreshed)["f_a"].severity) == (
        11.0,
        "high",
    )
    # Turning a filter on or off is the review's own choice, kept across a refresh.
    assert not community(refreshed)["f_a"].selected
    assert not community(refreshed)["f_b"].selected


def test_refresh_keeps_enabled_states_and_brings_new_expressions_to_unedited_filters(
    masters, review
):
    words = {"action": "mute", "category": "language", "subcategory": "swearing"}
    link = masters.link(
        USER,
        "v_1",
        "2",
        served_filter("f_on", 10_000, 11_000, expression="damn", **words),
        served_filter("f_off", 20_000, 21_000, expression="hell", **words),
        served_filter("f_mine", 30_000, 31_000, expression="heck", **words),
    )
    saved = save(masters, review, include(link["linkId"]))
    assert {fid: row.label for fid, row in community(saved).items()} == {
        "f_on": "damn",
        "f_off": "hell",
        "f_mine": "heck",
    }

    def edit(filters):
        for value in filters:
            if value["community_filter_id"] == "f_off":
                value.update(selected=False)
            if value["community_filter_id"] == "f_mine":
                value.update(label="Heck!", community_edited=True)

    edited = save(masters, review, keep(saved), edit=edit)
    assert not community(edited)["f_off"].community_edited
    _publish(
        masters,
        link,
        2,
        served_filter("f_on", 10_000, 11_000, expression="Damn", **words),
        served_filter("f_off", 20_000, 21_000, expression="Hell", **words),
        served_filter("f_mine", 30_000, 31_000, expression="heck", **words),
    )
    refreshed = community(save(masters, review, keep(edited, refresh=True)))
    assert {fid: (row.label, row.selected) for fid, row in refreshed.items()} == {
        "f_on": ("Damn", True),
        "f_off": ("Hell", False),
        "f_mine": ("Heck!", True),
    }


def test_a_filter_outside_the_file_is_added_once_the_master_moves_it_inside(masters, review):
    with masters.app.accounts.factory() as db:
        db.get(ScanRow, masters.scan_id).duration_seconds = 100
        db.get(ReviewSynchronizationRow, review).end_seconds = 100
        db.commit()
    link = masters.link(
        USER,
        "v_1",
        "2",
        served_filter("f_in", 10_000, 20_000),
        served_filter("f_out", 120_000, 130_000),
    )
    saved = save(masters, review, include(link["linkId"]))
    assert sorted(community(saved)) == ["f_in"]
    _publish(
        masters,
        link,
        2,
        served_filter("f_in", 10_000, 20_000),
        served_filter("f_out", 50_000, 60_000),
    )
    refreshed = save(masters, review, keep(saved, refresh=True))
    assert (
        community(refreshed)["f_out"].start_seconds,
        community(refreshed)["f_out"].selected,
    ) == (
        50.0,
        True,
    )


def test_locally_verified_uses_the_review_filters_own_bounds_even_when_detached(masters, review):
    link = masters.link(USER, "v_1", "2", served_filter("f_a", 50_000, 60_000))
    saved = save(masters, review, include(link["linkId"]))
    with masters.app.accounts.factory() as db:
        db.get(ScanRow, masters.scan_id).origin = "generated"
        db.add(
            ScanFindingRow(
                scan_id=masters.scan_id,
                category="violence",
                subcategory="kicking",
                start_seconds=100.5,
                end_seconds=101.0,
                source="visual",
            )
        )
        db.commit()
    assert not community(get_saved_review_for(masters, review))["f_a"].community.locally_verified

    def moved(filters):
        for value in filters:
            if value["community_filter_id"] == "f_a":
                value.update(start_seconds=100.0, end_seconds=102.0, community_edited=True)

    save(masters, review, keep(saved), edit=moved)
    with masters.app.accounts.factory() as db:
        from scanner.persistence.repository.review_community_sources import detach_link_sources

        detach_link_sources(db, [link["linkId"]])
        db.commit()
    detached = get_saved_review_for(masters, review)
    assert detached.community_sources[0].link_id is None
    assert community(detached)["f_a"].community.locally_verified


def get_saved_review_for(masters, review_id):
    with session(masters) as db:
        return get_saved_review(db, review_id)
