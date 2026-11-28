"""VideoSkip formats and local seed conversion."""

import json
from hashlib import sha256

import pytest

from scanner.commands.external_seed_cli import main
from scanner.formats.mcf_parser import parse_mcf_content
from scanner.formats.videoskip import MAX_CONTENT_BYTES, VideoSkipParseError, parse_videoskip

SKP = (
    "0:00:05.25\r\nStudio logo clears\r\n\r\n"
    "0:00:10.20 --> 0:00:12\r\npro word 2 (example)\r\n\r\n"
    "0:00:15 --> 0:00:19\r\nviolence 3\r\n\r\n"
    "0:00:20[0,0,50,50] --> 0:00:22\r\nnudity blur 3\r\n\r\n"
    '0:00:25 --> 0:00:27\r\nsex image 1\r\n\r\n'
    '0:00:28 --> 0:00:29\r\nother fast 1\r\n\r\n'
    '0:00:30 --> 0:00:31\r\nsex 1 // disabled\r\n\r\n'
    '{"netflix":0,"local":-0.25}\r\n\r\n'
    "data:image/jpeg;base64,YWJj\r\n"
).encode("utf-8-sig")


def test_videoskip_preserves_sync_and_does_not_apply_offsets():
    parsed = parse_videoskip(SKP)
    assert parsed.sha256 == sha256(SKP).hexdigest()
    assert parsed.sync.reference_seconds == 5.25
    assert parsed.sync.reference_description == "Studio logo clears"
    assert parsed.sync.platform_offsets_seconds == {"netflix": 0, "local": -0.25}
    assert parsed.sync.screenshot_sha256 == sha256(b"abc").hexdigest()
    assert parsed.sync.screenshot_media_type == "image/jpeg"
    assert [cue.source_action for cue in parsed.cues] == [
        "mute", "skip", "blur", "blank", "fast", "disabled",
    ]
    assert parsed.cues[2].region == "[0,0,50,50]"
    assert {issue.code for issue in parsed.issues} == {"unsupported_action"}
    audio, video = parsed.to_import().filters
    assert (audio.start_seconds, audio.end_seconds, audio.edit_action.value) == (10.2, 12, "mute")
    assert audio.mcf_channel == "audio"
    assert (audio.subcategory, audio.severity, audio.label) == ("swearing", "medium", "example")
    assert (video.mcf_channel, video.subcategory, video.edit_action) == ("both", "violence", "skip")


def test_first_range_is_a_cue_and_bad_rows_keep_line_numbers():
    parsed = parse_videoskip(
        b"\n0:00:02 --> 0:00:04.5profane word 1\n"
        b"0:00:06 --> 0:00:05\nsex 2\n"
        b"0:00:10 --> 0:00:12\nmade-up 2\n"
        b"00:13 --> 00:14\nother\n"
        b'{"local":NaN}\n'
    )
    assert parsed.sync.reference_seconds is None
    assert [row.start_seconds for row in parsed.to_import().filters] == [2, 13]
    assert parsed.to_import().filters[-1].severity is None
    assert [(issue.line_number, issue.code) for issue in parsed.issues] == [
        (3, "invalid_cue_range"), (5, "unknown_category"), (9, "invalid_sync_metadata"),
    ]


def test_gore_and_other_import_as_distinct_mcf_subcategories():
    parsed = parse_videoskip(b"0:00:10 --> 0:00:12\ngore 3\n\n0:00:14 --> 0:00:17\nother 1\n")
    assert not parsed.issues
    assert [(cue.category, cue.subcategory) for cue in parsed.to_import().filters] == [
        ("violence", "gore"),
        ("dispensable_scenes", "other"),
    ]


def test_parser_bounds_and_encoding():
    with pytest.raises(VideoSkipParseError, match="size limit"):
        parse_videoskip(b"a" * (MAX_CONTENT_BYTES + 1))
    with pytest.raises(VideoSkipParseError, match="UTF-8"):
        parse_videoskip(b"\xff")


def test_build_local_seed_retains_attribution_and_omitted_ranges(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "54.skp").write_bytes(SKP)
    links = archive / "manifest.json"
    links.write_text(json.dumps({
        "reuse_status": "local only",
        "files": [{
            "id": 54, "status": "downloaded", "file": "54.skp", "title": "Movie",
            "uploader": "Example", "imdb_id": "tt1234567",
            "sha256": sha256(SKP).hexdigest(),
            "source_url": "https://videoskip.herokuapp.com/exchange/skip/54/",
        }],
    }))
    guide = tmp_path / "filmiway.json"
    guide.write_text(json.dumps({
        "reuse_status": "local only",
        "unrecovered_guide_urls": [{"source_url": "https://www.filmiway.com/movie/missing/"}],
        "movies": [{
            "title": "Movie", "year": 2026, "edition": "Extended cut", "runtime_minutes": 2,
            "source_url": "https://www.filmiway.com/movie/movie/skip-timestamps",
            "scenes": [
                {"start_seconds": 5, "end_seconds": 8, "source_category": "Sex & Nudity",
                 "source_severity": "Moderate", "source_action": "skip"},
                {"start_seconds": 125, "end_seconds": 128, "source_category": "Nudity",
                 "source_severity": "High", "source_action": "skip", "timing_issue": "Past end"},
            ],
        }],
    }))
    output = tmp_path / "seed"
    main(["--videoskip", str(links), "--filmiway", str(guide), "--output", str(output)])
    videoskip = json.loads((output / "videoskip.json").read_text())["records"][0]
    assert videoskip["attribution"]["uploader"] == "Example"
    assert (output / videoskip["source_file"]).read_bytes() == SKP
    assert videoskip["sync"]["platform_offsets_seconds"]["local"] == -0.25
    assert len(videoskip["source_cues"]) == 6
    assert len(videoskip["filters"]) == 2
    film = json.loads((output / "filmiway.json").read_text())["records"][0]
    assert film["issues"] == [{"code": "outside_reported_runtime", "cue_index": 2}]
    assert film["source_cues"][1]["end_seconds"] == 128
    mcf = parse_mcf_content((output / film["import_file"]).read_bytes())
    assert mcf.source_metadata.source_url == film["attribution"]["source_url"]
    assert mcf.source_metadata.release == "Extended cut"
    assert {cue.subcategory.value for cue in mcf.entries} == {"nudity", "sex"}
