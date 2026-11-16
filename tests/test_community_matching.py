"""Approved compatibility core and conservative pending-stream behavior."""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from scanner.formats.community.fingerprints import FULL_ALGORITHM, SAMPLED_ALGORITHM
from scanner.modeling.community_matching import (
    POLICY_VERSION,
    FingerprintEvidence,
    MatchEvidence,
    StreamEvidence,
    classify,
    classify_candidates,
    compatibility_cache_key,
)
from scanner.modeling.media_identity import (
    AudioEvidence,
    DurationEvidence,
    EpisodeNumber,
    MediaIdentity,
    ProviderIdentifier,
    ReleaseEvidence,
)


def evidence(*, seconds=6000, source="file_probe", algorithm=SAMPLED_ALGORITHM):
    return MatchEvidence(
        MediaIdentity(
            "movie",
            (ProviderIdentifier("tmdb", "movie", "1", "user_confirmed"),),
            release=ReleaseEvidence(
                scene_name="Movie-GROUP",
                group="GROUP",
                edition="Theatrical",
                size_bytes=100,
                duration=DurationEvidence(seconds, source),
                audio=AudioEvidence("en", "main"),
            ),
        ),
        (FingerprintEvidence(algorithm, "a" * 64, 100),),
    )


def change_release(item, **changes):
    return replace(item, media=replace(item.media, release=replace(item.media.release, **changes)))


@pytest.mark.parametrize(
    "difference,band",
    [
        (0, "strong_fingerprint"),
        (2, "strong_fingerprint"),
        (-2, "strong_fingerprint"),
        (2.001, "incompatible"),
    ],
)
def test_approved_duration_boundary(difference, band):
    result = classify(evidence(), evidence(seconds=6000 + difference))
    assert result.video.band == band
    assert result.video.strong_eligible is (band == "strong_fingerprint")
    assert result.audio.band == ("incompatible" if band == "incompatible" else "possible_cut")
    assert not result.audio.strong_eligible
    assert result.selected is False


def test_full_catalog_and_unknown_audio():
    assert (
        classify(evidence(algorithm=FULL_ALGORITHM), evidence(algorithm=FULL_ALGORITHM)).video.band
        == "exact_file"
    )
    result = classify(evidence(), evidence(source="jellyfin_ticks"))
    assert result.video.band == "possible_cut"
    assert "measured_duration" in result.video.missing
    result = classify(evidence(), change_release(evidence(), audio=None))
    assert result.video.band == "strong_fingerprint"
    assert result.audio.missing == ("analyzed_audio_stream", "audio_track_metadata")


def test_audio_conflicts_do_not_block_video():
    result = classify(
        evidence(), change_release(evidence(), audio=AudioEvidence("fr", "commentary"))
    )
    assert result.video.band == "strong_fingerprint"
    assert result.audio.band == "incompatible"
    assert result.audio.conflicts == ("audio_language", "audio_role")


def test_work_and_edition_override_hash():
    target = evidence()
    wrong_work = replace(
        target,
        media=replace(
            target.media, identifiers=(ProviderIdentifier("tmdb", "movie", "2", "user_confirmed"),)
        ),
    )
    assert classify(target, wrong_work).video.conflicts == ("work_identity",)
    assert classify(target, change_release(target, edition="Extended")).video.band == "incompatible"
    no_ids = replace(target, media=replace(target.media, identifiers=()))
    assert classify(target, no_ids).video.band == "title_only"
    assert "work_identity" in classify(target, no_ids).video.missing


def test_span_and_alternative_numbering():
    base = evidence()
    number = EpisodeNumber("aired", 0, 1)
    target = replace(
        base,
        media=replace(
            base.media,
            entity_type="episode",
            identifiers=(),
            series_identifiers=(ProviderIdentifier("tvdb", "series", "7", "sonarr.series"),),
            episode_numbers=(number,),
            episode_span=(number, EpisodeNumber("aired", 0, 2)),
        ),
    )
    assert classify(target, target).video.band == "strong_fingerprint"
    single = replace(target, media=replace(target.media, episode_span=()))
    assert "episode_span" in classify(target, single).video.conflicts
    alternative = replace(
        target,
        media=replace(target.media, episode_numbers=(EpisodeNumber("dvd", 0, 1),), episode_span=()),
    )
    assert classify(target, alternative).video.band == "title_only"
    assert "episode_numbering" in classify(target, alternative).video.missing


def test_full_mismatch_prevents_sample_promotion():
    target = replace(
        evidence(),
        fingerprints=(FingerprintEvidence(FULL_ALGORITHM, "b" * 64, 100), *evidence().fingerprints),
    )
    candidate = replace(
        target,
        fingerprints=(FingerprintEvidence(FULL_ALGORITHM, "c" * 64, 100), *evidence().fingerprints),
    )
    result = classify(target, candidate)
    assert result.video.band == "release_match"
    assert "full_digest_differs" in result.video.reasons


def test_order_bounds_and_cache_version():
    target = evidence()
    assert [
        identifier
        for identifier, _ in classify_candidates(target, [("z", target), ("a", target)], limit=2)
    ] == ["a", "z"]
    for candidates, limit in [
        ([("a", target), ("b", target)], 1),
        ([("a", target), ("a", target)], 2),
        ([], 0),
    ]:
        with pytest.raises(ValueError):
            classify_candidates(target, candidates, limit=limit)
    assert compatibility_cache_key(target, target) != compatibility_cache_key(
        target, target, policy_version="future-test-policy"
    )
    assert compatibility_cache_key(target, target) != compatibility_cache_key(
        target, evidence(seconds=6001)
    )


def test_input_rejects_contradictory_size_and_duplicate_algorithms():
    with pytest.raises(ValueError):
        change_release(evidence(), size_bytes=101)
    with pytest.raises(ValueError):
        replace(evidence(), fingerprints=evidence().fingerprints * 2)
    with pytest.raises(ValueError):
        FingerprintEvidence(SAMPLED_ALGORITHM, "a" * 64, True)


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/community/compatibility-core-v1.json").read_text()
)


def merge(base, changes):
    result = dict(base)
    for key, value in changes.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else value
        )
    return result


def from_mapping(payload):
    return MatchEvidence(
        TypeAdapter(MediaIdentity).validate_python(payload["media"]),
        tuple(FingerprintEvidence(**item) for item in payload["fingerprints"]),
        video_stream=StreamEvidence(**payload["video_stream"])
        if payload.get("video_stream")
        else None,
        audio_stream=StreamEvidence(**payload["audio_stream"])
        if payload.get("audio_stream")
        else None,
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_shared_compatibility_cases(case):
    target = merge(FIXTURE["target"], case.get("target", {}))
    candidate = merge(target, case["candidate"])
    result = classify(from_mapping(target), from_mapping(candidate))
    assert result.policy_version == POLICY_VERSION
    assert result.video.band == case["video"]
    assert result.audio.band == case["audio"]
    assert list(result.video.conflicts) == case["video_conflicts"]
    assert list(result.audio.conflicts) == case["audio_conflicts"]
    assert "analyzed_audio_stream" in result.audio.missing
    assert not result.audio.strong_eligible
    assert not result.selected
    legacy_result = {**asdict(result), "policy_version": FIXTURE["policy_version"]}
    encoded = json.dumps(legacy_result, sort_keys=True, separators=(",", ":")).encode("ascii")
    assert hashlib.sha256(encoded).hexdigest() == FIXTURE["dto_sha256"][case["name"]]


@pytest.mark.parametrize("change", ["number", "series", "alternative"])
def test_episode_conflicts_and_unmapped_alternatives(change):
    payload = merge(FIXTURE["target"], FIXTURE["cases"][-2]["target"])
    target = from_mapping(payload)
    if change == "series":
        candidate = replace(
            target,
            media=replace(
                target.media,
                series_identifiers=(ProviderIdentifier("tvdb", "series", "8", "sonarr.series"),),
            ),
        )
        assert "series_identity" in classify(target, candidate).video.conflicts
    else:
        candidate = replace(
            target,
            media=replace(
                target.media,
                episode_numbers=(
                    EpisodeNumber("dvd" if change == "alternative" else "aired", 0, 3),
                ),
                episode_span=(),
            ),
        )
        result = classify(target, candidate)
        assert result.video.band == ("title_only" if change == "alternative" else "incompatible")


def test_band_priority_and_full_sample_contradiction():
    target = evidence()
    uncertain = replace(target, fingerprints=())
    for candidates in ([("a", uncertain), ("z", target)], [("z", target), ("a", uncertain)]):
        assert [
            identifier for identifier, _ in classify_candidates(target, candidates, limit=2)
        ] == ["z", "a"]
    target = replace(
        target,
        fingerprints=(FingerprintEvidence(FULL_ALGORITHM, "b" * 64, 100), *target.fingerprints),
    )
    candidate = replace(
        target,
        fingerprints=(
            target.fingerprints[0],
            FingerprintEvidence(SAMPLED_ALGORITHM, "c" * 64, 100),
        ),
    )
    assert "fingerprint_evidence" in classify(target, candidate).video.conflicts


def test_independent_stream_matches_and_exact_timing():
    from scanner.formats.community.stream_fingerprints import AUDIO_ALGORITHM, VIDEO_ALGORITHM

    target = replace(
        evidence(),
        fingerprints=(),
        video_stream=StreamEvidence(VIDEO_ALGORITHM, "b" * 64, "0", "6000"),
        audio_stream=StreamEvidence(AUDIO_ALGORITHM, "c" * 64, "0", "6000", "analyzed"),
    )
    assert classify(target, target).audio.band == "strong_fingerprint"
    assert classify(target, target).video.band == "strong_fingerprint"
    changed = replace(target, audio_stream=replace(target.audio_stream, digest="d" * 64))
    assert classify(target, changed).video.band == "strong_fingerprint"
    assert not classify(target, changed).audio.strong_eligible
    changed = replace(target, video_stream=replace(target.video_stream, digest="e" * 64))
    assert classify(target, changed).audio.band == "strong_fingerprint"
    assert not classify(target, changed).video.strong_eligible
    shifted = replace(target, audio_stream=replace(target.audio_stream, playback_start="1/1000000"))
    assert classify(target, shifted).audio.conflicts == ("audio_stream_playback_start",)
    assert classify(target, shifted).video.band == "strong_fingerprint"
    equivalent = replace(target, audio_stream=replace(target.audio_stream, duration="12000/2"))
    assert classify(target, equivalent).audio.band == "strong_fingerprint"
    unknown = replace(target, audio_stream=replace(target.audio_stream, provenance="selected"))
    assert not classify(target, unknown).audio.strong_eligible
    assert "analyzed_audio_stream" in classify(target, unknown).audio.missing
    for candidates in ([("a", unknown), ("z", target)], [("z", target), ("a", unknown)]):
        assert [
            identifier for identifier, _ in classify_candidates(target, candidates, limit=2)
        ] == ["z", "a"]


STREAM_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/community/compatibility-stream-v1.json").read_text()
)


@pytest.mark.parametrize("case", STREAM_FIXTURE["cases"], ids=lambda case: case["name"])
def test_shared_stream_compatibility_cases(case):
    target = merge(merge(FIXTURE["target"], STREAM_FIXTURE["target"]), case.get("target", {}))
    candidate = merge(target, case["candidate"])
    result = classify(from_mapping(target), from_mapping(candidate))
    assert result.policy_version == STREAM_FIXTURE["policy_version"]
    assert result.selected is False
    for channel_name in ("video", "audio"):
        result_channel = getattr(result, channel_name)
        assert result_channel.band == case[channel_name]
        assert list(result_channel.conflicts) == case.get(f"{channel_name}_conflicts", [])
        assert set(case.get(f"{channel_name}_missing", [])) <= set(result_channel.missing)
        assert result_channel.strong_eligible is (
            case[channel_name] in {"exact_file", "strong_fingerprint"}
        )
    encoded = json.dumps(asdict(result), sort_keys=True, separators=(",", ":")).encode("ascii")
    assert hashlib.sha256(encoded).hexdigest() == STREAM_FIXTURE["dto_sha256"][case["name"]]


@pytest.mark.parametrize(
    "timing", ["1/0", "1/-2", "NaN", "1e3", "9223372036854775808", "1/9223372036854775808"]
)
def test_stream_timing_bounds(timing):
    with pytest.raises(ValueError):
        StreamEvidence("future-stream-2", "a" * 64, timing, "1")


def test_stream_cache_and_input_guards():
    from scanner.formats.community.stream_fingerprints import AUDIO_ALGORITHM

    target = replace(
        evidence(), audio_stream=StreamEvidence(AUDIO_ALGORITHM, "a" * 64, "0", "1", "analyzed")
    )
    for changes in (
        {"playback_start": "1"},
        {"provenance": "unknown"},
        {"description": "renamed"},
        {"digest": "b" * 64},
    ):
        changed = replace(target, audio_stream=replace(target.audio_stream, **changes))
        assert compatibility_cache_key(target, target) != compatibility_cache_key(target, changed)
    with pytest.raises(ValueError, match="channel mismatch"):
        replace(target, video_stream=target.audio_stream)
    for changes in (
        {"duration": "0"},
        {"duration": "-1"},
        {"description": "x" * 513},
        {"provenance": "requested"},
        {"digest": "a" * 63},
    ):
        with pytest.raises(ValueError):
            replace(target.audio_stream, **changes)


@pytest.mark.parametrize(
    "field,other,value",
    [("playback_start", "duration", "1"), ("duration", "playback_start", "5999")],
)
def test_known_stream_conflict_survives_other_missing_timing(field, other, value):
    target = from_mapping(merge(FIXTURE["target"], STREAM_FIXTURE["target"]))
    candidate = replace(
        target, audio_stream=replace(target.audio_stream, **{field: value, other: None})
    )
    result = classify(target, candidate)
    assert result.audio.band == "incompatible"
    assert result.audio.conflicts == (f"audio_stream_{field}",)
    assert "audio_stream_timing" in result.audio.missing
    assert result.video.band == "strong_fingerprint"
