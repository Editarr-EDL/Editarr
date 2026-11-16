"""What revision 0018 adds to a database snapshot, for the earlier revisions' upgrade tests."""


def drop_0018_additions(upgraded: dict) -> None:
    """Remove 0018's additions from `upgraded` in place after checking their values.

    The Jellyfin account sync table starts empty and the new last
    `user_provider_identities` column is null. Snapshots leave out the `users` status
    columns, which 0030 changes (`test_remove_passwords_migration`).
    """

    upgraded.pop("review_policy_evidence")
    assert upgraded.pop("review_scan_repairs") == []
    assert upgraded.pop("media_item_export_regenerations") == []
    assert upgraded.pop("account_link_transitions") == []
    assert upgraded.pop("account_link_export_moves") == []
    assert upgraded.pop("account_dispositions") == []
    assert upgraded.pop("account_transfer_moves") == []
    assert upgraded.pop("jellyfin_user_blocks") == []
    assert all(row[-5:] == (None, None, None, 0, None) for row in upgraded["media_item_exports"])
    upgraded["media_item_exports"] = [row[:-5] for row in upgraded["media_item_exports"]]
    assert upgraded.pop("jellyfin_account_sync") == []
    identities = upgraded["user_provider_identities"]
    assert [row[-1] for row in identities] == [None] * len(identities)
    upgraded["user_provider_identities"] = [row[:-1] for row in identities]
