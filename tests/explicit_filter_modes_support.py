"""What revision 0031 changes in a database snapshot, for earlier revisions' upgrade tests."""

_COPIES = ("user_filter_copy_rules", "user_filter_copy_severities")
FILTER_MODE_TABLES = ("user_filter_preferences", *_COPIES)


def drop_0031_additions(upgraded: dict, original: dict) -> None:
    """Put back the filter rows 0031 changed in `upgraded`, in place, after checking them.

    0031 gives every account a mode and each copy-mode account without a copy a
    copy of the instance defaults (`test_explicit_filter_modes_migration`). Earlier
    rows keep their revisions, and no earlier copy row changes.
    """

    preferences = upgraded["user_filter_preferences"]
    assert sorted(row[0] for row in preferences) == sorted(row[0] for row in upgraded["users"])
    assert {row[1] for row in preferences} <= {"live", "copy"}
    kept = {row[0]: row[2:4] for row in original.get("user_filter_preferences", [])}
    assert {row[0]: row[2:4] for row in preferences if row[0] in kept} == kept
    for table in _COPIES:
        assert set(original.get(table, [])) <= set(upgraded[table])
    for table in FILTER_MODE_TABLES:
        upgraded[table] = original.get(table, [])
