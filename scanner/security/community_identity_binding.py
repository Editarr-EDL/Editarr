"""Whether a database's community connection belongs to a signing identity."""

from .community_identity import InstallationIdentity


def projects_identity(
    identity: InstallationIdentity,
    *,
    installation_id: str | None,
    contributor_id: str | None,
    service_url: str,
    key_id: str | None,
) -> bool:
    """Whether an enrolled connection row records `identity`.

    The row may still name the old key of a completed rotation or recovery whose
    new key it has not yet recorded. Any other key is a different generation.
    """
    if (installation_id, service_url) != (
        identity.installation_id,
        identity.service_url or (service_url if identity.contributor_id is None else None),
    ) or contributor_id not in {None, identity.contributor_id}:
        return False
    change = identity.rotation or identity.recovery
    return key_id == identity.key_id or bool(
        change
        and change["state"] == "completed"
        and key_id == change.get("localOldKeyId", change["oldKeyId"])
    )
