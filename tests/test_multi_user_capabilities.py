"""Check capability defaults against the published permission tables."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.catalog import (
    ACCOUNT_PROTECTION,
    CAPABILITIES,
    CAPABILITY_CONTRACT_VERSION,
    DELEGABLE,
    INVARIANT_ROWS,
    contract_digest,
)
from scanner.security.access.catalog_operations import SETTINGS_DECISION_ROWS
from scanner.security.access.overrides import (
    OverrideRejected,
    parse_override,
    parse_role_default,
)
from scanner.security.access.vocabulary import (
    TARGET_RULES,
    AccountClass,
    Authentication,
    Facet,
    OwnerPower,
    Role,
)

DOCS = Path(__file__).resolve().parents[1] / "docs"
PERMISSIONS = (DOCS / "multi-user-permissions.md").read_text(encoding="utf-8")

GRANTING_CELLS = {
    "Yes",
    "Yes, configurable",
    "Permission-scoped",
    "Account/media-scoped",
    "Own identity",
    "Own and ordinary users",
}
INVARIANT_CELLS = {"Excluded", "Outside scope"}

OWNER_POWER_PHRASES = {
    OwnerPower.TRANSFER_OWNERSHIP: "transfer ownership",
    OwnerPower.MANAGE_OWNER_ACCOUNT: "manage the owner account",
    OwnerPower.EDIT_ROLE_DEFAULTS: "edit role defaults",
    OwnerPower.GRANT_ROLES_OR_OVERRIDES: (
        "grant/revoke admin roles or individual permission overrides"
    ),
    OwnerPower.REVEAL_OTHERS_API_KEYS: "reveal others' API keys",
    OwnerPower.CHANGE_CONNECTIONS_OR_DEPLOYMENT: "change connections/deployment",
    OwnerPower.DOWNLOAD_BACKUPS: "download backups",
    OwnerPower.MANAGE_ENFORCED_FILTER_POLICY: "manage enforced filter policy",
}

FRESH_PHRASES = {
    "API-key reveal/rotation": {
        C.API_KEYS_CREATE_OWN,
        C.API_KEYS_ROTATE_OWN,
        C.API_KEYS_REVEAL_OWN,
        C.API_KEYS_ISSUE_OTHER,
        C.API_KEYS_REVEAL_OTHER,
    },
    "permission changes": {
        C.ROLES_DEFAULTS_EDIT,
        C.ROLES_ASSIGN,
        C.PERMISSIONS_OVERRIDES_EDIT,
        C.MEDIA_ACCESS_DEFAULTS_EDIT,
        C.MEDIA_ACCESS_USER_EDIT,
        C.FILTERS_POLICY_ENFORCE,
    },
    "account linking": {C.ACCOUNTS_LINK_OWN, C.ACCOUNTS_LINK_OTHER},
    "account deletion": {C.ACCOUNTS_DELETE_OTHER},
    "Jellyfin unblocking": {C.ACCOUNTS_JELLYFIN_UNBLOCK},
    "ownership transfer": {C.OWNERSHIP_TRANSFER},
}


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _table_rows(text: str, header: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("| ") or line.startswith((header, "| ---")):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert cells[0] not in rows, f"duplicate row {cells[0]}"
        rows[cells[0]] = cells[1:]
    return rows


ROLE_MATRIX, SETTINGS_MATRIX = PERMISSIONS.split("## Settings permission defaults", 1)
MATRIX = _table_rows(ROLE_MATRIX, "| Action |")
SETTINGS_TABLE = _table_rows(SETTINGS_MATRIX, "| Action |")
# Restore runs during onboarding, before any role exists.
SETTINGS_WITHOUT_ROLE = {"Restore a backup"}
ROLE_COLUMNS = (Role.USER, Role.ADMIN, Role.OWNER)


def _grants(cell: str, facet: Facet) -> bool:
    if cell in GRANTING_CELLS:
        return True
    if cell == "No":
        return False
    if cell in {"Own", "Own reviews"}:
        assert facet is not Facet.ANY, "an Own cell needs an own or other capability"
        return facet is Facet.OWN
    if cell == "Ordinary users":
        return facet is not Facet.OWN
    raise AssertionError(f"Unrecognized matrix cell {cell!r}")


def _rows_to_capabilities() -> dict[str, set[C]]:
    mapped: dict[str, set[C]] = {}
    for row in CAPABILITIES.values():
        for action in row.matrix_rows:
            mapped.setdefault(action, set()).add(row.capability)
    return mapped


def test_matrix_is_parsed_completely():
    assert len(MATRIX) == 70
    assert all(len(cells) == 3 for cells in MATRIX.values())


def test_every_matrix_row_maps_to_capabilities_or_one_invariant():
    mapped = _rows_to_capabilities()
    assert set(mapped) | set(INVARIANT_ROWS) == set(MATRIX)
    assert not set(mapped) & set(INVARIANT_ROWS)
    for action in INVARIANT_ROWS:
        assert set(MATRIX[action]) <= INVARIANT_CELLS, action


@pytest.mark.parametrize("capability", sorted(CAPABILITIES), ids=str)
def test_role_defaults_match_every_cited_matrix_row(capability):
    row = CAPABILITIES[capability]
    facet = TARGET_RULES[row.target].facet
    for action in row.matrix_rows:
        cells = dict(zip(ROLE_COLUMNS, MATRIX[action], strict=True))
        for role in (Role.USER, Role.ADMIN):
            assert (role in row.granted) is _grants(cells[role], facet), (action, role)
        assert cells[Role.OWNER] in {"Yes", "Own identity"}, action


@pytest.mark.parametrize("action", sorted(SETTINGS_DECISION_ROWS))
def test_settings_defaults_match_documented_minimum_roles(action):
    minimum = Role(SETTINGS_TABLE[action][0])
    expected = {Role.USER: {Role.USER, Role.ADMIN}, Role.ADMIN: {Role.ADMIN}, Role.OWNER: set()}
    for capability in SETTINGS_DECISION_ROWS[action]:
        assert set(CAPABILITIES[capability].granted) == expected[minimum], capability


def test_settings_table_is_fully_mapped():
    assert set(SETTINGS_DECISION_ROWS) | SETTINGS_WITHOUT_ROLE == set(SETTINGS_TABLE)


def test_nondelegable_owner_powers_follow_the_matrix_paragraph():
    paragraph = _flat(PERMISSIONS.split("## Nondelegable and authentication restrictions", 1)[1])
    assert set(OWNER_POWER_PHRASES) == set(OwnerPower)
    for power, phrase in OWNER_POWER_PHRASES.items():
        assert phrase in paragraph, phrase
    holders = {power: set() for power in OwnerPower}
    for row in CAPABILITIES.values():
        if row.owner_power is not None:
            holders[row.owner_power].add(row.capability)
            assert row.granted == frozenset()
            assert row.capability not in DELEGABLE
    assert holders.pop(OwnerPower.MANAGE_OWNER_ACCOUNT) == set()
    assert ACCOUNT_PROTECTION[AccountClass.OWNER] is OwnerPower.MANAGE_OWNER_ACCOUNT
    assert all(holders.values()), holders


@pytest.mark.parametrize("capability", sorted(set(C) - DELEGABLE), ids=str)
@pytest.mark.parametrize("effect", ["allow", "deny"])
def test_overrides_and_role_defaults_never_accept_nondelegable_capabilities(capability, effect):
    expected = "role_fixed_capability" if capability is C.ARCHIVE_VIEW else "owner_only_capability"
    with pytest.raises(OverrideRejected) as override:
        parse_override(capability.value, effect)
    assert override.value.code == expected
    with pytest.raises(OverrideRejected) as default:
        parse_role_default("admin", capability.value, True)
    assert default.value.code == expected


def test_only_the_archive_is_fixed_to_its_role():
    assert {c for c, row in CAPABILITIES.items() if row.role_fixed} == {C.ARCHIVE_VIEW}
    assert CAPABILITIES[C.ARCHIVE_VIEW].granted == frozenset({Role.ADMIN})


def test_delegable_overrides_and_defaults_parse():
    assert parse_override("reviews.view.other", "deny").capability is C.REVIEWS_VIEW_OTHER
    assert parse_role_default("user", "scans.start", False).granted is False
    for raw, code in [("unknown.capability", "unknown_capability")]:
        with pytest.raises(OverrideRejected) as rejected:
            parse_override(raw, "allow")
        assert rejected.value.code == code
    with pytest.raises(OverrideRejected) as owner:
        parse_role_default("owner", "scans.start", False)
    assert owner.value.code == "owner_role_fixed"
    with pytest.raises(OverrideRejected) as effect:
        parse_override("scans.start", "grant")
    assert effect.value.code == "invalid_override_effect"


@pytest.mark.parametrize("granted", ["false", "true", "", 0, 1, None])
def test_role_defaults_require_an_actual_boolean(granted):
    with pytest.raises(OverrideRejected) as rejected:
        parse_role_default("user", "scans.start", granted)
    assert rejected.value.code == "invalid_role_default"


@pytest.mark.parametrize("effect", [True, 1, None, "ALLOW", " allow"])
def test_override_effects_must_be_an_exact_effect_string(effect):
    with pytest.raises(OverrideRejected) as rejected:
        parse_override("scans.start", effect)
    assert rejected.value.code == "invalid_override_effect"


@pytest.mark.parametrize("capability", [None, 1, "SCANS.START"])
def test_override_capabilities_must_be_an_exact_capability_id(capability):
    with pytest.raises(OverrideRejected) as rejected:
        parse_override(capability, "allow")
    assert rejected.value.code == "unknown_capability"


def test_fresh_interactive_requirements_follow_the_matrix_paragraph():
    paragraph = _flat(PERMISSIONS.split("## Nondelegable and authentication restrictions", 1)[1])
    for phrase in FRESH_PHRASES:
        assert phrase in paragraph, phrase
    fresh = {
        capability
        for capability, row in CAPABILITIES.items()
        if row.authentication is Authentication.FRESH_INTERACTIVE
    }
    assert fresh == set().union(*FRESH_PHRASES.values())


def test_enforcing_filter_policy_requires_fresh_owner_authentication():
    restrictions = PERMISSIONS.split("## Nondelegable and authentication restrictions", 1)[1]
    assert "manage enforced filter policy" in _flat(restrictions)
    assert "fresh authentication" in _flat(restrictions)
    row = CAPABILITIES[C.FILTERS_POLICY_ENFORCE]
    assert row.authentication is Authentication.FRESH_INTERACTIVE
    assert row.owner_power is OwnerPower.MANAGE_ENFORCED_FILTER_POLICY


def test_contract_digest_is_pinned_to_its_version():
    assert CAPABILITY_CONTRACT_VERSION == "editarr-capabilities-9"
    assert contract_digest() == "a3c9b242cf4785d533e79843e3e77688c412430759f27924fc489174c245a9d4"


def test_every_web_fixture_names_the_current_contract_version():
    web = DOCS.parent / "web"
    stale = {
        f"{path.relative_to(web)}: {version}"
        for folder in ("src", "e2e")
        for path in (web / folder).rglob("*.ts*")
        for version in re.findall(r"editarr-capabilities-\d+", path.read_text("utf-8"))
        if version != CAPABILITY_CONTRACT_VERSION
    }
    assert not stale
