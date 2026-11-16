"""Semantic parity for experimental community encodings."""

import base64
import json
from pathlib import Path

import pytest

from scripts.benchmark_community_encoding import (
    ARTIFACT,
    decode_proto,
    encode_json,
    encode_proto,
    fixtures,
    summary,
    vectors,
)


@pytest.mark.parametrize("name", ["small", "large", "zero", "partial", "manual", "derived"])
def test_experimental_codec_parity(name):
    fixture = fixtures()[name]
    assert json.loads(encode_json(fixture)) == fixture
    assert decode_proto(encode_proto(fixture)) == fixture
    d1 = summary(fixture)
    assert json.loads(encode_json(d1)) == d1
    assert decode_proto(encode_proto(d1, as_summary=True), as_summary=True) == d1


def test_optional_identity_preserves_unknown_vs_explicit_empty():
    unknown = fixtures()["partial"]
    explicit_empty = json.loads(encode_json(unknown))
    explicit_empty["stages"][-1]["model_id"] = ""
    assert encode_proto(unknown) != encode_proto(explicit_empty)
    assert decode_proto(encode_proto(unknown))["stages"][-1]["model_id"] is None
    assert decode_proto(encode_proto(explicit_empty))["stages"][-1]["model_id"] == ""


def test_unknown_proto_field_is_preserved_but_not_interpreted():
    raw = encode_proto(fixtures()["small"])
    # Field 127, wire type length-delimited, one-byte value; not in experimental v1.
    extended = raw + bytes((0xFA, 0x07, 0x01, ord("x")))
    assert decode_proto(extended) == decode_proto(raw)
    old_reader = ARTIFACT()
    old_reader.ParseFromString(extended)
    assert old_reader.SerializeToString(deterministic=True) == extended


def test_worker_vectors_match_python_reference():
    path = (
        Path(__file__).resolve().parents[1]
        / "community-service/test/fixtures/encoding-vectors.json"
    )
    exported = json.loads(path.read_text(encoding="utf-8"))
    assert exported["descriptor_base64"] == base64.b64encode(
        ARTIFACT.DESCRIPTOR.file.serialized_pb
    ).decode()
    assert exported["vectors"] == vectors()
