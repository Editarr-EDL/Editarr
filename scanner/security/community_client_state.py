"""Validated private enrollment journal, atomically published with registration."""

import json
import re
import unicodedata

from .private_identity_files import IdentityHome, IdentityUnavailable


def validate_state(state: object, manifest: dict) -> None:
    """Reject unknown journal formats rather than silently losing pending authority."""
    if state is None:
        return
    try:
        required = {"version", "pending", "profile"}
        if not isinstance(state, dict) or set(state) not in (required, required | {"rename"}):
            raise ValueError
        if state["version"] != 1:
            raise ValueError
        pending = state["pending"]
        if pending is not None:
            if set(pending) != {"service_url", "audience", "secret", "state_id", "expires_at"}:
                raise ValueError
            from scanner.integrations.community.transport import ServiceEndpoint

            ServiceEndpoint(pending["service_url"], pending["audience"])
            if not re.fullmatch(r"[0-9a-f]{64}", pending["secret"]):
                raise ValueError
            if pending["state_id"] is not None and not re.fullmatch(
                r"[0-9a-f]{64}", pending["state_id"]
            ):
                raise ValueError
            if type(pending["expires_at"]) is not int or pending["expires_at"] < 0:
                raise ValueError
        profile = state["profile"]
        if profile is not None:
            validate_profile(
                profile, manifest["installation_id"], manifest["key_id"], manifest["contributor_id"]
            )
        rename = state.get("rename")
        if rename is not None:
            if (
                set(rename) != {"operation_id", "display_name"}
                or not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", rename["operation_id"])
                or not isinstance(rename["display_name"], str)
                or not 3 <= len(rename["display_name"]) <= 40
                or profile is None
            ):
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as error:
        raise IdentityUnavailable("corrupt", "Invalid community client state") from error


def validate_profile(
    value: object, installation_id: str, key_id: str, contributor_id: str | None
) -> dict:
    """Project only public hosted fields; verify that the result belongs to this key."""
    from scanner.integrations.community.transport import CommunityFailure

    required = {
        "contributorId",
        "shortId",
        "installationId",
        "keyId",
        "keyState",
        "displayName",
        "profileRevision",
        "policyVersion",
        "protocolVersion",
        "allowedOperations",
    }
    if not isinstance(value, dict) or not required <= value.keys():
        raise CommunityFailure("invalid_response")
    if (
        value["installationId"] != installation_id
        or value["keyId"] != key_id
        or value["keyState"] != "active"
        or value["protocolVersion"] != 1
        or type(value["protocolVersion"]) is not int
        or not isinstance(value["contributorId"], str)
        or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value["contributorId"]) is None
        or contributor_id not in {None, value["contributorId"]}
        or value["shortId"] != value["contributorId"][:8]
        or not isinstance(value["displayName"], str)
        or not 3 <= len(value["displayName"]) <= 40
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value["displayName"]
        )
        or type(value["profileRevision"]) is not int
        or value["profileRevision"] < 0
        or not isinstance(value["policyVersion"], str)
        or not 1 <= len(value["policyVersion"]) <= 128
        or not isinstance(value["allowedOperations"], list)
        or any(
            not isinstance(item, str) or item not in {"identity:read", "identity:rename"}
            for item in value["allowedOperations"]
        )
    ):
        raise CommunityFailure("invalid_response")
    return {name: value[name] for name in required}


def update_client_state(
    home: IdentityHome,
    *,
    expected: dict | None,
    state: dict,
    contributor_id: str | None = None,
    service_url: str | None = None,
):
    """Compare-and-publish the journal and optional authoritative registration together."""
    from .community_identity import (
        MANIFEST_FILE,
        _atomic_write,
        _directory,
        _load,
        _locked,
        _manifest_for,
        _validate_registration,
    )

    root = _directory(home)
    with _locked(home.data_dir):
        identity = _load(root)
        if identity.pending_replacement is not None or (
            identity.recovery and identity.recovery["state"] == "pending"
        ):
            raise IdentityUnavailable("pending", "Finish the pending key rotation first")
        if identity.client_state != expected:
            raise IdentityUnavailable("pending", "Community state changed; reload before retrying")
        manifest = _manifest_for(identity)
        if contributor_id is not None:
            if identity.contributor_id not in {None, contributor_id}:
                raise IdentityUnavailable("corrupt", "Community contributor cannot change silently")
            _validate_registration(contributor_id, service_url, "active")
            manifest.update(
                contributor_id=contributor_id,
                service_url=service_url,
                last_known_key_state="active",
            )
        manifest["client_state"] = state
        validate_state(state, manifest)
        raw = json.dumps(manifest, separators=(",", ":"), ensure_ascii=True).encode()
        if len(raw) > 16_384:
            raise IdentityUnavailable("unavailable", "Community state exceeds manifest limit")
        _atomic_write(root / MANIFEST_FILE, raw)
        return _load(root)
