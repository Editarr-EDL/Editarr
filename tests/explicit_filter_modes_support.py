"""Compare persisted filter settings across schema revisions."""

from scanner.models import MCF_1_1_1_CATEGORIES

_COPIES = ("user_filter_copy_rules", "user_filter_copy_severities")
FILTER_MODE_TABLES = ("user_filter_preferences", *_COPIES)


def drop_filter_migration_additions(upgraded: dict, original: dict) -> None:
    """Remove verified filter-setting additions when comparing older database snapshots.

    0031 gives every account a mode and each copy-mode account without a copy a
    copy of the instance defaults (`test_explicit_filter_modes_migration`). Earlier
    rows keep their revisions, and no earlier copy row changes. 0041 appends
    medium severity defaults and copy severities for new MCF categories.
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
    upgraded["mcf_severity_defaults"] = legacy_instance_severities(
        upgraded["mcf_severity_defaults"]
    )


def legacy_instance_severities(rows: list[tuple]) -> list[tuple]:
    """Verify and exclude the eight newly appended instance default rows."""
    legacy = [row for row in rows if row[1] not in MCF_1_1_1_CATEGORIES]
    additions = sorted(set(rows) - set(legacy), key=lambda row: row[2])
    assert {row[1] for row in additions} == MCF_1_1_1_CATEGORIES
    assert [row[2] for row in additions] == list(range(len(legacy), len(rows)))
    assert all(row[0] == 1 and row[3] == "medium" for row in additions)
    return legacy
