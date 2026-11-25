"""Field rules of `community-artifact-3` (§5.2): each refused shape and its service code,
accepted variants, and language-entry expressions."""

import copy

import pytest

from scanner.formats.community.artifact_v3 import ArtifactV3Error, decode_artifact_v3
from scanner.formats.community.artifact_v3_models import normalize_expression
from scanner.modeling.community_normalization import normalize
from tests.test_community_artifact_v3 import artifact_value, raw_gzip


def mutate(path: str, new: object):
    def apply(value: dict) -> None:
        *parents, last = path.split(".")
        target = value
        for part in parents:
            target = target[int(part)] if part.isdigit() else target[part]
        if new is DELETE:
            del target[last]
        elif last.isdigit():
            target[int(last)] = new
        else:
            target[last] = new

    return apply


DELETE = object()
SUPPORT_TEMPLATE = artifact_value()["entries"][0]


def add_entry(**changes):
    def apply(value: dict) -> None:
        entry = copy.deepcopy(SUPPORT_TEMPLATE)
        entry.update({"entryId": "e9", "proposedFilterId": "f_new", **changes})
        value["entries"].append(entry)

    return apply


INVALID = {
    "schema": (mutate("schemaVersion", "community-artifact-2"), "invalid_submission"),
    "unknown field": (mutate("extra", 1), "invalid_submission"),
    "snake case": (mutate("author.contributor_id", "x"), "invalid_submission"),
    "missing nullable": (mutate("alignmentHint", DELETE), "invalid_submission"),
    "bad token": (mutate("submissionId", "a b"), "invalid_submission"),
    "bad key id": (mutate("author.keyId", "sha256:XYZ"), "invalid_submission"),
    "float createdAt": (mutate("createdAt", 1.0), "invalid_submission"),
    "bool as int": (mutate("media.durationMs", True), "invalid_submission"),
    "no identity": (mutate("work.tmdb", None), "work_identity_required"),
    "bad provider id": (mutate("work.tmdb", "60a"), "invalid_submission"),
    "movie season": (mutate("work.season", 1), "invalid_submission"),
    "episode missing": (mutate("work.kind", "episode"), "invalid_submission"),
    "empty normalized title": (mutate("work.title", "!!!"), "invalid_submission"),
    "year": (mutate("work.year", 1869), "invalid_submission"),
    "alias id": (mutate("version.aliasId", "Theatrical"), "invalid_submission"),
    "other text too long": (
        mutate("version", {"choice": "other", "text": "x" * 65}),
        "invalid_submission",
    ),
    "duration zero": (mutate("media.durationMs", 0), "invalid_submission"),
    "duration": (mutate("media.durationMs", 86_400_001), "invalid_submission"),
    "duplicate key type": (
        mutate(
            "media.matchKeys.1",
            {"type": "sha256_sampled", "value": "d" * 64, "sizeBytes": 4823749120},
        ),
        "invalid_submission",
    ),
    "size mismatch": (mutate("media.matchKeys.1.sizeBytes", 1), "invalid_submission"),
    "md5 length": (mutate("media.matchKeys.1.value", "b" * 64), "invalid_submission"),
    "tier 4 key": (
        mutate("media.matchKeys.2", {"type": "tmdb", "value": "603"}),
        "invalid_submission",
    ),
    "stream algorithm": (mutate("media.matchKeys.2.algorithm", "x"), "invalid_submission"),
    "scale": (mutate("alignmentHint.scale", 1.2), "invalid_submission"),
    "revision": (mutate("alignmentHint.revision", 0), "invalid_submission"),
    "anchors": (mutate("alignmentHint.anchors", 2001), "invalid_submission"),
    "coverage category": (mutate("coverage.0.category", "gore"), "invalid_taxonomy"),
    "coverage duplicate": (
        lambda v: v["coverage"].append(copy.deepcopy(v["coverage"][0])),
        "invalid_submission",
    ),
    "coverage empty": (mutate("coverage.0.intervals", []), "invalid_submission"),
    "coverage beyond": (mutate("coverage.0.intervals.0.endMs", 8160513), "invalid_bounds"),
    "coverage overlap": (
        mutate("coverage.0.intervals", [{"startMs": 0, "endMs": 10}, {"startMs": 5, "endMs": 20}]),
        "invalid_bounds",
    ),
    "entry id": (mutate("entries.1.entryId", "e1"), "invalid_submission"),
    "entry id length": (mutate("entries.0.entryId", "e" * 33), "invalid_submission"),
    "entry kind": (mutate("entries.0.kind", "vote"), "invalid_submission"),
    "filter id reuse": (mutate("entries.1.proposedFilterId", "f_9c1d"), "invalid_submission"),
    "dispute without filter": (mutate("entries.1.proposedFilterId", None), "invalid_submission"),
    "coverage negative without filter": (
        add_entry(
            kind="coverage_negative",
            subcategory=None,
            severity=None,
            action=None,
            proposedFilterId=None,
        ),
        "invalid_submission",
    ),
    "support missing severity": (mutate("entries.0.severity", None), "invalid_submission"),
    "dispute classified": (mutate("entries.1.action", "skip"), "invalid_submission"),
    "entry category": (mutate("entries.0.category", "gore"), "invalid_taxonomy"),
    "subcategory family": (mutate("entries.0.subcategory", "swearing"), "invalid_taxonomy"),
    "start equals end": (mutate("entries.0.endMs", 600000), "invalid_bounds"),
    "end beyond duration": (mutate("entries.0.endMs", 8160513), "invalid_bounds"),
    "origin": (mutate("entries.0.evidence.origin", "scan"), "invalid_submission"),
    "model without version": (mutate("entries.0.evidence.origin", "model"), "invalid_submission"),
    "model version length": (
        mutate("entries.0.evidence.modelVersion", "m" * 129),
        "invalid_submission",
    ),
    "expression on non-language": (mutate("entries.0.expression", "damn"), "invalid_submission"),
    "expression on dispute": (mutate("entries.1.expression", "damn"), "invalid_submission"),
    "expression blank": (mutate("entries.2.expression", "  "), "invalid_submission"),
    "expression untrimmed": (mutate("entries.2.expression", " damn"), "invalid_submission"),
    "expression not NFC": (mutate("entries.2.expression", "cre\u0300me"), "invalid_submission"),
    "expression control": (mutate("entries.2.expression", "da\u0007mn"), "invalid_submission"),
    "expression format": (mutate("entries.2.expression", "da\u200bmn"), "invalid_submission"),
    "expression too long": (mutate("entries.2.expression", "x" * 65), "invalid_submission"),
    "expression type": (mutate("entries.2.expression", 7), "invalid_submission"),
    "title bytes": (mutate("work.title", "é" * 257), "invalid_submission"),
    "normalized title code points": (mutate("work.title", "a b " * 64 + "c"), "invalid_submission"),
}


@pytest.mark.parametrize("name", INVALID)
def test_validation_failures_are_rejected_with_service_codes(name):
    change, code = INVALID[name]
    value = artifact_value()
    change(value)
    with pytest.raises(ArtifactV3Error) as error:
        decode_artifact_v3(raw_gzip(value))
    assert error.value.code == code


def test_valid_kinds_and_null_identity_variants_are_accepted():
    value = artifact_value()
    value["work"].update(tmdb=None, tvdb="121361")
    value["version"] = {"choice": "other", "text": None}
    value["alignmentHint"] = None
    value["entries"][0]["evidence"].update(origin="model", modelVersion="m-1")
    add_entry(kind="coverage_negative", subcategory=None, severity=None, action=None)(value)
    value["coverage"][0]["intervals"] = [{"startMs": 0, "endMs": 10}, {"startMs": 10, "endMs": 20}]
    assert len(decode_artifact_v3(raw_gzip(value)).artifact.entries) == 4


def test_language_expressions_normalize_and_may_be_absent():
    assert normalize_expression("  Cre\u0300me brûlée\u00a0") == "Crème brûlée"
    assert normalize_expression("😀" * 64) == "😀" * 64
    assert normalize_expression("😀" * 65) is None
    assert normalize_expression("da\nmn") is None and normalize_expression(None) is None
    value = artifact_value()
    value["entries"][2]["expression"] = "😀" * 64
    assert decode_artifact_v3(raw_gzip(value)).artifact.entries[2].expression == "😀" * 64
    for entry in value["entries"]:
        del entry["expression"]
    assert [item.expression for item in decode_artifact_v3(raw_gzip(value)).artifact.entries] == [
        None,
        None,
        None,
    ]


def test_normalized_title_may_have_exactly_256_code_points():
    value = artifact_value()
    value["work"]["title"] = "  " + "a b " * 63 + "abcd!!"
    decoded = decode_artifact_v3(raw_gzip(value))
    assert len(normalize(decoded.artifact.work.title)) == 256


def test_empty_model_version_follows_the_contract():
    entry = decode_artifact_v3(raw_gzip(artifact_value())).artifact.entries[1]
    assert (entry.evidence.origin, entry.evidence.model_version) == ("model", "")


def test_lone_surrogates_are_not_expressions():
    assert normalize_expression("da\ud800mn") is None
    assert normalize_expression("\udc00") is None
    assert normalize_expression("😀 damn") == "😀 damn"
    value = artifact_value()
    value["entries"][2]["expression"] = "da\ud800mn"
    with pytest.raises(ArtifactV3Error) as error:
        decode_artifact_v3(raw_gzip(value))
    assert error.value.code == "invalid_submission"
