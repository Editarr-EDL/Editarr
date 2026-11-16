"""Cross-repository export identity, path, and selection golden fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from scanner.formats.edl import export_edl_content, parse_edl_content
from scanner.formats.export_layout import (
    EXPORT_LAYOUT_VERSION,
    ExportId,
    ExportLayoutError,
    JellyfinUserId,
    LocalExportId,
    default_export_path,
    legacy_export_path,
    media_stem,
    new_local_export_id,
    parse_export_id,
    parse_jellyfin_user_id,
    personal_export_path,
)
from scanner.mcf import McfParseError, export_mcf_content, parse_mcf_content
from scanner.models import EditAction, ExportFormat, ScanFinding

FIXTURE = Path(__file__).parent / "fixtures" / "multi-user" / "export-layout-v1"
MANIFEST_BYTES = (FIXTURE / "manifest.json").read_bytes()
MANIFEST = json.loads(MANIFEST_BYTES)
PRINCIPALS = {
    name: parse_export_id(principal["exportId"])
    for name, principal in MANIFEST["principals"].items()
}
PATH_STYLES = {"posix": PurePosixPath, "windows": PureWindowsPath}

_PLUGIN_EDL_ACTIONS = frozenset({"0", "1", "2", "3", "cut", "mute", "scene", "commercial"})
_PLUGIN_EDL_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _plugin_edl_valid(content: bytes) -> bool:
    """Reference for the plugin's EdlParser.Validate over the manifest's pinned domain.

    It mirrors File.ReadAllLinesAsync line splitting at the manifest's source
    commit. It is not .NET runtime evidence; MU-05 runs the same vectors there.
    """

    text = content.decode("utf-8", errors="replace").removeprefix("\ufeff")
    for raw_line in re.split(r"\r\n|\r|\n", text):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        fields = line.split()
        if len(fields) < 3 or not all(_PLUGIN_EDL_NUMBER.fullmatch(f) for f in fields[:2]):
            return False
        if float(fields[1]) <= float(fields[0]) or fields[2].lower() not in _PLUGIN_EDL_ACTIONS:
            return False
    return True


def _payload(name: str) -> bytes:
    return (FIXTURE / MANIFEST["payloads"][name]["file"]).read_bytes()


def _valid(content: bytes, export_format: ExportFormat) -> bool:
    if export_format is ExportFormat.EDL:
        return _plugin_edl_valid(content)
    try:
        parse_mcf_content(content)
    except McfParseError:
        return False
    return True


def _select(media: Path, export_id: ExportId, export_format: ExportFormat):
    """Reference selection: personal, then default only when personal is absent."""

    candidates = (
        ("personal", personal_export_path(media, export_id, export_format)),
        ("default", default_export_path(media, export_format)),
    )
    for scope, path in candidates:
        try:
            entry = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return f"{scope}_unreadable", None
        if not stat.S_ISREG(entry.st_mode):
            return f"{scope}_invalid", None
        try:
            content = path.read_bytes()
        except OSError:
            return f"{scope}_unreadable", None
        if not _valid(content, export_format):
            return f"{scope}_invalid", None
        return scope, content
    return "none", None


def test_manifest_is_versioned_and_pinned():
    assert MANIFEST["contract"] == EXPORT_LAYOUT_VERSION == "editarr-export-layout-1"
    assert (
        hashlib.sha256(MANIFEST_BYTES).hexdigest()
        == "c3063858e3ecf2f8982c66a1140f52ab7d806e737ff5c7854b413cdecccf987e"
    )


@pytest.mark.parametrize("name", sorted(MANIFEST["payloads"]))
def test_payload_bytes_match_their_digest_and_validity(name):
    entry = MANIFEST["payloads"][name]
    content = _payload(name)
    assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    assert _valid(content, ExportFormat(entry["format"])) is entry["valid"]


def test_edl_grammar_pins_the_plugin_source_revision():
    source = MANIFEST["edlGrammar"]["source"]
    assert source["commit"] == "7797442e8d23b8c5d0cbe6d554927c27c70b052e"
    assert source["files"] == [
        "src/Jellyfin.Plugin.Edl/Services/EdlParser.cs",
        "src/Jellyfin.Plugin.Edl/Api/EdlController.cs",
    ]


@pytest.mark.parametrize(
    "vector", MANIFEST["edlGrammar"]["vectors"], ids=lambda vector: vector["name"]
)
def test_edl_grammar_vectors_follow_the_plugin_parser(vector):
    assert _plugin_edl_valid(vector["content"].encode("utf-8")) is vector["valid"]


def test_editarr_import_still_accepts_a_file_the_plugin_rejects():
    content = _payload("edl-unsupported-action")
    assert not _plugin_edl_valid(content)
    imported = parse_edl_content(content)
    assert [(row.start_seconds, row.end_seconds) for row in imported.filters] == [(0, 1)]
    assert imported.ignored_action_count == 1


def _finding(action: EditAction, start: float, end: float) -> ScanFinding:
    return ScanFinding(
        id=1,
        scan_id="scan",
        category=None,
        subcategory=None,
        start_seconds=start,
        end_seconds=end,
        confidence=1,
        source="test",
        selected=True,
        edit_action=action,
    )


@pytest.mark.parametrize(
    ("name", "findings"),
    [
        ("edl-personal-a", [_finding(EditAction.MUTE, 2, 4)]),
        ("edl-personal-b", [_finding(EditAction.SKIP, 10, 12)]),
        ("edl-default", [_finding(EditAction.SKIP, 5, 8)]),
        ("edl-legacy", [_finding(EditAction.SKIP, 20, 21)]),
        ("edl-local", [_finding(EditAction.MUTE, 30, 31)]),
    ],
)
def test_valid_edl_payloads_are_editarr_exporter_output(name, findings):
    assert export_edl_content(findings).encode("utf-8") == _payload(name)


@pytest.mark.parametrize(
    "name",
    [
        name
        for name, entry in MANIFEST["payloads"].items()
        if entry["format"] == "mcf" and entry["valid"]
    ],
)
def test_valid_mcf_payloads_are_canonical_editarr_exports(name):
    parsed = parse_mcf_content(_payload(name))
    assert export_mcf_content(parsed.source_metadata, parsed.entries) == _payload(name)


def test_personal_mcf_payload_is_the_existing_exporter_golden():
    golden = Path(__file__).parent / "fixtures" / "mcf" / "exported-1.1.mcf"
    assert _payload("mcf-personal-a") == golden.read_bytes()


@pytest.mark.parametrize("case", MANIFEST["jellyfinUserIdInputs"], ids=lambda case: case["input"])
def test_provider_jellyfin_ids_normalize_or_refuse(case):
    if "canonical" in case:
        assert parse_jellyfin_user_id(case["input"]) == JellyfinUserId(case["canonical"])
    else:
        with pytest.raises(ExportLayoutError) as refused:
            parse_jellyfin_user_id(case["input"])
        assert refused.value.code == case["error"]


@pytest.mark.parametrize("case", MANIFEST["exportIdSegments"], ids=lambda case: case["segment"])
def test_stored_export_ids_must_already_be_canonical(case):
    if "kind" in case:
        parsed = parse_export_id(case["segment"])
        expected = LocalExportId if case["kind"] == "local" else JellyfinUserId
        assert isinstance(parsed, expected)
        assert parsed.value == case["segment"]
    else:
        with pytest.raises(ExportLayoutError) as refused:
            parse_export_id(case["segment"])
        assert refused.value.code == case["error"]


def test_principals_derive_from_their_provider_input():
    for name, principal in MANIFEST["principals"].items():
        if principal["kind"] == "jellyfin":
            assert parse_jellyfin_user_id(principal["providerInput"]) == PRINCIPALS[name]


def test_new_local_ids_are_canonical_and_distinct():
    first, second = new_local_export_id(), new_local_export_id()
    assert parse_export_id(first.value) == first
    assert first != second


@pytest.mark.parametrize("case", MANIFEST["mediaNames"], ids=lambda case: case["name"])
def test_media_stems_match_both_runtimes(case):
    if "stem" in case:
        assert media_stem(case["name"]) == case["stem"]
    else:
        with pytest.raises(ExportLayoutError) as refused:
            media_stem(case["name"])
        assert refused.value.code == case["error"]


@pytest.mark.parametrize(
    "case", MANIFEST["paths"], ids=lambda case: f"{case['style']}-{case['format']}"
)
def test_exact_path_examples(case):
    media = PATH_STYLES[case["style"]](case["media"])
    export_format = ExportFormat(case["format"])
    export_id = PRINCIPALS[case["principal"]]
    assert str(personal_export_path(media, export_id, export_format)) == case["personal"]
    assert str(default_export_path(media, export_format)) == case["default"]
    assert str(legacy_export_path(media, export_format)) == case["legacy"]


@pytest.mark.parametrize("case", MANIFEST["selection"], ids=lambda case: case["name"])
def test_selection_cases_on_a_real_directory(case, tmp_path):
    if any(entry.get("unreadable") for entry in case["entries"]) and os.geteuid() == 0:
        pytest.skip("root can read mode 000 files")
    for entry in case["entries"]:
        target = tmp_path.joinpath(*entry["path"].split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("directory"):
            target.mkdir()
            continue
        target.write_bytes(_payload(entry["payload"]))
        if entry.get("unreadable"):
            target.chmod(0)
    try:
        outcome, content = _select(
            tmp_path / case["media"], PRINCIPALS[case["principal"]], ExportFormat(case["format"])
        )
    finally:
        for path in tmp_path.rglob("*"):
            path.chmod(stat.S_IRWXU)
    assert outcome == case["expect"]["outcome"]
    expected_payload = case["expect"].get("payload")
    assert content == (_payload(expected_payload) if expected_payload else None)
