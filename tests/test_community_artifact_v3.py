import base64
import copy
import gzip
import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scanner.formats.community.artifact_v3 import (
    ArtifactV3Error,
    ArtifactV3Limits,
    artifact_file_keys,
    artifact_json_bytes,
    build_manifest_v3,
    decode_artifact_v3,
    encode_artifact_v3,
    parse_manifest_v3,
)
from scanner.formats.community.artifact_v3_models import ArtifactV3, normalize_expression
from scanner.formats.community.strict_json import (
    StrictJsonError,
    canonical_json_bytes,
    js_number,
)
from scanner.modeling.community_match_keys import HashKey, StreamKey
from scanner.modeling.community_normalization import normalize
from scanner.security.community_artifact_signatures import (
    ArtifactSignatureError,
    HistoricalKey,
    signature_base,
)
from scanner.security.community_artifact_v3_signatures import (
    build_signed_submission,
    sign_artifact_v3,
    submission_request_body,
    verify_artifact_v3_signature,
)

FIXTURES = Path(__file__).parents[1] / "community-service/test/fixtures/community-v3"
VECTOR = json.loads((FIXTURES / "artifact-v3.json").read_text(encoding="utf-8"))
KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(VECTOR["authorPrivateKeySeedHex"]))
PUBLIC = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
OTHER_PUBLIC = (
    Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
)
KEY_ID = VECTOR["authorKeyId"]


def artifact_value() -> dict:
    return copy.deepcopy(VECTOR["artifact"])


def raw_gzip(value: object) -> bytes:
    return gzip.compress(json.dumps(value).encode(), mtime=0)


def model() -> ArtifactV3:
    return ArtifactV3.model_validate(artifact_value(), by_alias=True, by_name=False)


def test_round_trip_and_canonical_bytes_are_stable():
    first = encode_artifact_v3(model())
    second = encode_artifact_v3(decode_artifact_v3(first.gzip_bytes).artifact)
    assert first.gzip_bytes == second.gzip_bytes
    assert first.artifact == model()
    assert first.gzip_bytes[3] == 0 and first.gzip_bytes[4:8] == b"\0\0\0\0"
    text = first.json_bytes.decode()
    assert text.startswith(
        '{"schemaVersion":"community-artifact-3","submissionId":"sub_01J9Z3K5C6Q"'
    )
    assert '"scale":1,' in text
    assert " " not in text.replace("The Matrix", "").replace("Crème brûlée", "")
    assert json.loads(text) == artifact_value()
    assert first.digest == hashlib.sha256(first.gzip_bytes).hexdigest()
    assert artifact_file_keys(first.artifact) == (
        HashKey("sha256_sampled", "a" * 64, 4823749120),
        HashKey("md5_quick", "b" * 32, 4823749120),
        StreamKey(
            "audio_stream", "editarr-community-audio-pcm-s16le-16000-mono-sha256-1", "c" * 64
        ),
    )


def test_unicode_is_encoded_as_utf8_not_escaped():
    value = artifact_value()
    value["work"]["title"] = "Amélie"
    encoded = encode_artifact_v3(ArtifactV3.model_validate(value, by_alias=True))
    assert "Amélie".encode() in encoded.json_bytes


def test_episode_work_round_trips():
    value = artifact_value()
    value["work"].update(
        kind="episode", tmdb=None, tvdb="81189", season=1, episode=2, lastEpisode=17
    )
    decoded = decode_artifact_v3(raw_gzip(value))
    assert decoded.artifact.work.last_episode == 17


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


def deflate_member(data: bytes, flags: int = 0, header_extra: bytes = b"") -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = compressor.compress(data) + compressor.flush()
    header = b"\x1f\x8b\x08" + bytes([flags]) + b"\0\0\0\0\x02\xff" + header_extra
    return header + body + struct.pack("<II", zlib.crc32(data), len(data))


@pytest.mark.parametrize(
    "transform",
    [
        lambda data: deflate_member(data, 0x08, b"name\0"),
        lambda data: deflate_member(data, 0x04, b"\0\0"),
        lambda data: deflate_member(data, 0x10, b"c\0"),
        lambda data: deflate_member(data, 0x02, b"\0\0"),
        lambda data: deflate_member(data) + deflate_member(b""),
        lambda data: deflate_member(data) + b"\0",
        lambda data: deflate_member(data)[:-4] + b"\0\0\0\0",
        lambda data: deflate_member(b"\xef\xbb\xbf" + data),
        lambda data: deflate_member(data.replace(b"\xc3\xa9", b"\xe9")),
        lambda data: deflate_member(data.replace(b'"createdAt":', b'"createdAt":1,"createdAt":')),
        lambda data: deflate_member(
            data.replace(b'"createdAt":1795553648', b'"createdAt":1795553648e0')
        ),
        lambda data: deflate_member(
            data.replace(b'"createdAt":1795553648', b'"createdAt":9007199254740992')
        ),
        lambda data: deflate_member(data.replace(b'"scale":1', b'"scale":NaN')),
        lambda data: data,
    ],
)
def test_transport_level_failures_are_rejected(transform):
    value = artifact_value()
    value["work"]["title"] = "Amélie"
    data = artifact_json_bytes(ArtifactV3.model_validate(value, by_alias=True))
    assert decode_artifact_v3(deflate_member(data)).artifact.work.title == "Amélie"
    with pytest.raises(ArtifactV3Error):
        decode_artifact_v3(transform(data))


def test_resource_limits_are_enforced():
    encoded = encode_artifact_v3(model())
    with pytest.raises(ArtifactV3Error):
        decode_artifact_v3(encoded.gzip_bytes, limits=ArtifactV3Limits(compressed_bytes=10))
    with pytest.raises(ArtifactV3Error):
        decode_artifact_v3(encoded.gzip_bytes, limits=ArtifactV3Limits(expanded_bytes=100))
    with pytest.raises(ArtifactV3Error):
        decode_artifact_v3(encoded.gzip_bytes, limits=ArtifactV3Limits(depth=4))
    value = artifact_value()
    value["entries"][0]["evidence"]["modelVersion"] = "x" * 513
    with pytest.raises(ArtifactV3Error):
        decode_artifact_v3(raw_gzip(value))
    value = artifact_value()
    value["entries"] = [
        {**copy.deepcopy(SUPPORT_TEMPLATE), "entryId": f"e{i}", "proposedFilterId": None}
        for i in range(2001)
    ]
    with pytest.raises(ArtifactV3Error):
        decode_artifact_v3(raw_gzip(value))


def test_manifest_is_exact_and_bound_to_artifact():
    decoded = encode_artifact_v3(model())
    manifest = build_manifest_v3(decoded)
    assert (
        manifest
        == (
            '{"schema_version":"community-manifest-1","artifact_format":"gzip-json",'
            '"artifact_schema_version":"community-artifact-3",'
            f'"artifact_digest":"{decoded.digest}","submission_id":"sub_01J9Z3K5C6Q",'
            f'"contributor_id":"c_7f3a","key_id":"{KEY_ID}","summary":{{}}}}'
        ).encode()
    )
    assert parse_manifest_v3(manifest, decoded=decoded).artifact_digest == decoded.digest
    original = json.loads(manifest)
    for change in (
        {"summary": {"total": 1}},
        {"artifact_digest": "0" * 64},
        {"submission_id": "other"},
        {"contributor_id": "other"},
        {"artifact_schema_version": "community-artifact-2"},
        {"extra": True},
    ):
        with pytest.raises(ArtifactV3Error):
            parse_manifest_v3(json.dumps({**original, **change}).encode(), decoded=decoded)
    with pytest.raises(ArtifactV3Error):
        parse_manifest_v3(manifest + b" " * 4096, decoded=decoded)


def historical(status: str = "active", **changes) -> HistoricalKey:
    return HistoricalKey(
        **{
            "contributor_id": "c_7f3a",
            "key_id": KEY_ID,
            "public_key": PUBLIC,
            "status": status,
            **changes,
        }
    )


def verify(signed, **changes):
    arguments = {
        "artifact_bytes": signed.artifact_bytes,
        "manifest_bytes": signed.manifest_bytes,
        "envelope_bytes": signed.envelope_bytes,
        "historical_key": historical(),
        "expected_submission_id": "sub_01J9Z3K5C6Q",
        **changes,
    }
    return verify_artifact_v3_signature(**arguments)


def test_sign_and_verify_v3_submission():
    signed = build_signed_submission(model(), private_key=KEY)
    envelope = json.loads(signed.envelope_bytes)
    assert list(envelope) == ["schema_version", "signature"]
    assert envelope["schema_version"] == "community-artifact-signature-1"
    verified = verify(signed)
    assert verified.decoded.artifact == model()
    assert verify(signed, historical_key=historical("retired")).manifest == verified.manifest
    base = signature_base(signed.manifest_bytes, verified.manifest)
    assert base.startswith(
        b"editarr-community-artifact-signature-v1\0\0\0\0\x14community-manifest-1"
    )
    assert base[-64:] == (
        hashlib.sha256(signed.manifest_bytes).digest()
        + hashlib.sha256(signed.artifact_bytes).digest()
    )
    again = sign_artifact_v3(
        private_key=KEY, artifact_bytes=signed.artifact_bytes, manifest_bytes=signed.manifest_bytes
    )
    assert again == signed
    body = submission_request_body(signed)
    assert body["submissionId"] == "sub_01J9Z3K5C6Q"
    assert base64.b64decode(body["artifact"]) == signed.artifact_bytes
    assert list(body) == ["version", "submissionId", "artifact", "manifest", "envelope"]


def test_tampered_or_untrusted_submissions_are_rejected():
    signed = build_signed_submission(model(), private_key=KEY)
    other = build_signed_submission(model().model_copy(update={"created_at": 1}), private_key=KEY)
    signature = json.loads(signed.envelope_bytes)["signature"]
    tampered_manifest = signed.manifest_bytes.replace(b"{}", b"{ }")
    for changes in (
        {"artifact_bytes": other.artifact_bytes},
        {"manifest_bytes": tampered_manifest},
        {"envelope_bytes": other.envelope_bytes},
        {"envelope_bytes": signed.envelope_bytes.replace(b":", b": ", 1)},
        {
            "envelope_bytes": json.dumps(
                {
                    "schema_version": "community-artifact-signature-1",
                    "signature": signature.rstrip("="),
                }
            ).encode()
        },
        {"historical_key": historical("revoked")},
        {"historical_key": historical(public_key=OTHER_PUBLIC)},
        {"expected_submission_id": "sub_other"},
    ):
        with pytest.raises(ArtifactSignatureError) as error:
            verify(signed, **changes)
        assert error.value.code == "invalid_submission"
    for key in (historical(contributor_id="c_other"), historical(key_id="sha256:" + "0" * 64)):
        with pytest.raises(ArtifactSignatureError) as error:
            verify(signed, historical_key=key)
        assert error.value.code == "key_mismatch"


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


def test_cross_language_artifact_vector():
    from scanner.testing.community_v3_vectors import vectors

    assert vectors()["artifact-v3.json"] == VECTOR
    gzip_bytes = base64.b64decode(VECTOR["gzipBase64"])
    manifest = VECTOR["manifest"].encode()
    decoded = decode_artifact_v3(gzip_bytes)
    assert decoded.json_bytes.decode() == VECTOR["canonicalJson"]
    assert artifact_json_bytes(decoded.artifact).decode() == VECTOR["canonicalJson"]
    assert canonical_json_bytes(VECTOR["artifact"]).decode() == VECTOR["canonicalJson"]
    assert decoded.digest == VECTOR["artifactDigest"]
    assert build_manifest_v3(decoded) == manifest
    parsed = parse_manifest_v3(manifest, decoded=decoded)
    assert signature_base(manifest, parsed).hex() == VECTOR["signatureBaseHex"]
    public = base64.b64decode(VECTOR["authorPublicKeyBase64"])
    verified = verify_artifact_v3_signature(
        artifact_bytes=gzip_bytes,
        manifest_bytes=manifest,
        envelope_bytes=VECTOR["envelope"].encode(),
        historical_key=HistoricalKey("c_7f3a", VECTOR["authorKeyId"], public, "active"),
        expected_submission_id="sub_01J9Z3K5C6Q",
    )
    assert verified.decoded.artifact.submission_id == "sub_01J9Z3K5C6Q"


def test_canonical_numbers_match_ecmascript_vectors():
    cases = json.loads((FIXTURES / "json-numbers.json").read_text())["cases"]
    assert len(cases) > 300
    for case in cases:
        value = struct.unpack(">d", bytes.fromhex(case["float64BigEndianHex"]))[0]
        assert js_number(value) == case["js"]
        assert canonical_json_bytes([value]) == f"[{case['js']}]".encode()
    for bad in (float("nan"), float("inf")):
        with pytest.raises(StrictJsonError):
            canonical_json_bytes(bad)
