import hashlib
import os

import pytest

from scanner.modeling.community_match_keys import (
    HashKey,
    ProviderKey,
    StreamKey,
    TitleYearKey,
    md5_quick_key,
)
from scanner.modeling.community_match_ladder import (
    MatchQuery,
    VersionCandidate,
    rank_versions,
    runtime_agreement,
    score_version,
)
from scanner.modeling.community_normalization import (
    SEEDED_ALIASES,
    THEATRICAL,
    Alias,
    AliasListError,
    ResolvedLabel,
    alias_spelling_index,
    labels_equal,
    normalize,
    parse_alias_list,
    resolve_other_text,
    resolve_query_label,
    title_year_key,
)

SIZE = 4_823_749_120
FULL = HashKey("sha256_full", "f" * 64, SIZE)
SAMPLED = HashKey("sha256_sampled", "a" * 64, SIZE)
MD5 = HashKey("md5_quick", "b" * 32, SIZE)
AUDIO = StreamKey("audio_stream", "editarr-community-audio-sha256-sampled-1", "c" * 64)
VIDEO = StreamKey("video_stream", "editarr-community-video-sha256-sampled-1", "d" * 64)
TMDB = ProviderKey("tmdb", "movie", "603")
TVDB = ProviderKey("tvdb", "movie", "169")
TITLE = TitleYearKey("The Matrix", 1999)
THEATRICAL_LABEL = ResolvedLabel(THEATRICAL, None)
RUNTIME = 8_160_500


def version(**changes) -> VersionCandidate:
    values = {
        "version_id": "v_1",
        "status": "confirmed",
        "label": THEATRICAL_LABEL,
        "runtime_ms": RUNTIME,
        "trust": 0.5,
        "file_keys": (FULL, SAMPLED, MD5, AUDIO, VIDEO),
        "work_keys": (TMDB, TVDB, TITLE),
    }
    return VersionCandidate(**{**values, **changes})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Director’s Cut", "directors cut"),
        ("Director's Cut", "directors cut"),
        ("Directorʼs`Cut", "directorscut"),
        ("  EXTENDED   Edition!", "extended edition"),
        ("Amélie", "amelie"),
        ("Spider-Man: No Way Home", "spider man no way home"),
        ("Ⅲ", "iii"),
        ("ＡＢＣ　１２３", "abc 123"),
        ("Straße", "straße"),
        ("İ", "i"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize_contract_cases(value, expected):
    assert normalize(value) == expected


def test_title_year_key():
    assert title_year_key("The Matrix", 1999) == "the matrix\x001999"


def test_seeded_aliases_and_spelling_resolution():
    index = alias_spelling_index(SEEDED_ALIASES)
    assert SEEDED_ALIASES[0].alias_id == THEATRICAL and "" in SEEDED_ALIASES[0].spellings
    assert index["directors cut"] == "directors_cut"
    assert index["original theatrical cut"] == THEATRICAL
    assert resolve_other_text("Director’s Edition", SEEDED_ALIASES) == ResolvedLabel(
        "directors_cut", "directors edition"
    )
    assert resolve_other_text("  ", SEEDED_ALIASES) == THEATRICAL_LABEL
    assert resolve_other_text(None, SEEDED_ALIASES) == THEATRICAL_LABEL
    assert resolve_other_text("Fan Edit", SEEDED_ALIASES) == ResolvedLabel(None, "fan edit")
    assert resolve_query_label(None, SEEDED_ALIASES) == THEATRICAL_LABEL
    assert resolve_query_label({"aliasId": "extended"}, ()) == ResolvedLabel("extended", None)
    assert resolve_query_label({"text": "Unrated Cut"}, SEEDED_ALIASES).alias_id == "unrated"
    with pytest.raises(ValueError):
        resolve_query_label({"aliasId": "x", "text": "y"}, ())
    with pytest.raises(AliasListError):
        alias_spelling_index([*SEEDED_ALIASES, Alias("other", "Other", ("Extended",))])


def test_parse_alias_list():
    body = {"version": 1, "aliases": [
        {"aliasId": "theatrical", "label": "Theatrical", "spellings": ["theatrical"]},
        {"aliasId": "imax", "label": "IMAX", "spellings": ["IMAX Edition"]},
    ]}
    aliases = parse_alias_list(body)
    assert aliases[1] == Alias("imax", "IMAX", ("imax edition",))
    for bad in (
        {**body, "extra": 1},
        {"version": 2, "aliases": []},
        {"version": 1, "aliases": [{"aliasId": "BAD", "label": "x", "spellings": []}]},
        {"version": 1, "aliases": [body["aliases"][0], body["aliases"][0]]},
    ):
        with pytest.raises(AliasListError):
            parse_alias_list(bad)


@pytest.mark.parametrize(
    ("left", "right", "equal"),
    [
        (ResolvedLabel(None, None), THEATRICAL_LABEL, True),
        (ResolvedLabel("extended", "extended"), ResolvedLabel("extended", None), True),
        (ResolvedLabel("extended", None), THEATRICAL_LABEL, False),
        (ResolvedLabel(None, "fan edit"), ResolvedLabel(None, "fan edit"), True),
        (ResolvedLabel(None, "fan edit"), ResolvedLabel(None, "other"), False),
        (ResolvedLabel(None, "fan edit"), THEATRICAL_LABEL, False),
        (ResolvedLabel(None, "fan edit"), ResolvedLabel(None, None), False),
    ],
)
def test_label_equality(left, right, equal):
    assert labels_equal(left, right) is equal
    assert labels_equal(right, left) is equal


@pytest.mark.parametrize(
    ("query_ms", "expected"),
    [
        (RUNTIME, "same_cut"),
        (RUNTIME + 2_000, "same_cut"),
        (RUNTIME - 2_000, "same_cut"),
        (RUNTIME + 2_001, "possible_cut"),
        (RUNTIME - 2_001, "possible_cut"),
        (RUNTIME + 30_000, "possible_cut"),
        (RUNTIME - 30_000, "possible_cut"),
        (RUNTIME + 30_001, "outside"),
        (RUNTIME - 30_001, "outside"),
        (None, "unknown"),
    ],
)
def test_runtime_agreement_boundaries(query_ms, expected):
    assert runtime_agreement(query_ms, RUNTIME) == expected


@pytest.mark.parametrize(
    ("keys", "label", "runtime_ms", "tier", "percent", "matched", "agreement"),
    [
        ((FULL, MD5), None, None, "1", 100, ("sha256_full",), "not_applicable"),
        ((SAMPLED, MD5, AUDIO), None, None, "2", 80, ("sha256_sampled", "md5_quick"),
         "not_applicable"),
        ((AUDIO, VIDEO, TMDB), ResolvedLabel("extended", None), RUNTIME + 99_999, "3", 65,
         ("audio_stream", "video_stream"), "not_applicable"),
        ((TMDB, TVDB, TITLE), None, RUNTIME + 2_000, "4", 30, ("tmdb", "tvdb"), "same_cut"),
        ((TMDB,), None, RUNTIME - 30_000, "4", 30, ("tmdb",), "possible_cut"),
        ((TITLE,), None, RUNTIME + 1, "5", 15, ("title_year",), "same_cut"),
        ((TITLE,), None, RUNTIME + 30_000, "5", 15, ("title_year",), "possible_cut"),
        ((TMDB, TITLE), None, RUNTIME + 30_001, "runtime_mismatch", 5, ("tmdb", "title_year"),
         "outside"),
        ((TITLE,), None, None, "runtime_mismatch", 5, ("title_year",), "unknown"),
        ((TMDB,), ResolvedLabel("extended", None), RUNTIME, "other_version", 0, ("tmdb",),
         "same_cut"),
        ((TITLE,), ResolvedLabel(None, "fan edit"), None, "other_version", 0, ("title_year",),
         "unknown"),
    ],
)
def test_every_ladder_tier(keys, label, runtime_ms, tier, percent, matched, agreement):
    query = MatchQuery(keys, label or THEATRICAL_LABEL, runtime_ms)
    score = score_version(query, version())
    assert (score.tier, score.match_percent, score.matched_keys, score.runtime_agreement) == (
        tier, percent, matched, agreement,
    )


def test_key_equality_rules_and_no_shared_key():
    target = version()
    for key in (
        HashKey("md5_quick", "b" * 32, SIZE + 1),
        StreamKey("audio_stream", "editarr-community-audio-pcm-s16le-16000-mono-sha256-1",
                  "c" * 64),
        ProviderKey("tmdb", "episode", "603", 1, 1),
        ProviderKey("tvdb", "movie", "603"),
        TitleYearKey("The Matrix", None),
        TitleYearKey("The Matrix", 2000),
    ):
        assert score_version(MatchQuery((key,), runtime_ms=RUNTIME), target) is None
    assert score_version(MatchQuery((TitleYearKey("the  MATRIX!", 1999),), runtime_ms=RUNTIME),
                         target).tier == "5"
    mismatched = HashKey("sha256_full", "0" * 64, SIZE)
    assert score_version(MatchQuery((mismatched, MD5)), target).tier == "2"
    episode = ProviderKey("tvdb", "episode", "81189", 1, 2, 3)
    assert score_version(
        MatchQuery((episode,), runtime_ms=RUNTIME), version(work_keys=(episode,))
    ).tier == "4"
    assert score_version(
        MatchQuery((ProviderKey("tvdb", "episode", "81189", 1, 2),), runtime_ms=RUNTIME),
        version(work_keys=(episode,)),
    ) is None


def test_unknown_version_runtime_is_a_runtime_mismatch():
    score = score_version(MatchQuery((TMDB,), runtime_ms=RUNTIME), version(runtime_ms=None))
    assert (score.tier, score.runtime_agreement) == ("runtime_mismatch", "unknown")


def test_result_order_and_limit():
    query = MatchQuery((MD5, TMDB), runtime_ms=RUNTIME)
    versions = [
        version(version_id="v_c", file_keys=(), runtime_ms=RUNTIME + 40_000),
        version(version_id="v_f", file_keys=(), runtime_ms=RUNTIME + 3_000),
        version(version_id="v_e", file_keys=(), runtime_ms=RUNTIME),
        version(version_id="v_d", file_keys=(), status="candidate", runtime_ms=RUNTIME),
        version(version_id="v_b", trust=0.1),
        version(version_id="v_a2", trust=0.9),
        version(version_id="v_a1", trust=0.9),
        version(version_id="v_g", file_keys=(), label=ResolvedLabel("unrated", None)),
        version(version_id="v_z", file_keys=(), work_keys=(ProviderKey("tmdb", "movie", "1"),)),
    ]
    ranked = [score.version_id for score, _ in rank_versions(query, versions)]
    assert ranked == ["v_a1", "v_a2", "v_b", "v_e", "v_f", "v_d", "v_c", "v_g"]
    assert len(rank_versions(query, versions, limit=2)) == 2
    with pytest.raises(ValueError):
        rank_versions(query, versions, limit=51)


def test_md5_quick_key_uses_existing_sampled_fingerprint(tmp_path, monkeypatch):
    from scanner.settings.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    small = tmp_path / "small.mkv"
    small.write_bytes(b"editarr" * 1000)
    assert md5_quick_key(small) == HashKey(
        "md5_quick", hashlib.md5(small.read_bytes()).hexdigest(), 7000
    )
    large = tmp_path / "large.mkv"
    edge = 10 * 1024 * 1024
    head, middle, tail = os.urandom(edge), b"\0" * (31 * 1024 * 1024), os.urandom(edge)
    large.write_bytes(head + middle + tail)
    key = md5_quick_key(large)
    assert key.value == hashlib.md5(head + tail).hexdigest()
    assert key.size_bytes == 2 * edge + len(middle)
