"""Exercise the actual embedded ASS extraction and reviewed audio transcript timing."""

import json
import shutil
import subprocess
from fractions import Fraction
from types import SimpleNamespace

import pytest

from scanner.formats.subtitle_redaction import SubtitleMute, redact_subtitles
from scanner.integrations.jellyfin_subtitles import JellyfinSubtitle
from scanner.modeling.model_subtitles import ReviewAudioSource, SubtitleSource
from scanner.pipeline.audio_tracks import (
    AudioSelectionError,
    AudioTrack,
    AudioTrackInventory,
    AudioTrackSelection,
    StaleAudioSelectionError,
)
from scanner.pipeline.subtitle_tracks import embedded_subtitle_streams
from scanner.runtime import subtitle_transcript
from scanner.runtime.scan_version import file_generation
from scanner.runtime.subtitle_inputs import read_subtitle
from scanner.runtime.subtitle_transcript import ReviewedAudio, transcript_document


def test_cached_transcript_retains_timed_words_around_empty_mlx_markers(tmp_path, monkeypatch):
    cache = tmp_path / "transcript_mlx_whisper_base.json"
    monkeypatch.setattr(subtitle_transcript, "_cache_paths", lambda _: [cache])
    marker = {"text": "", "start_seconds": 5, "end_seconds": 5, "words": []}
    speech = {
        "text": "hello",
        "start_seconds": 6,
        "end_seconds": 7,
        "words": [{"word": "hello", "start_seconds": 6, "end_seconds": 7}],
    }
    cache.write_text(json.dumps([marker, speech, {**marker, "start_seconds": 8, "end_seconds": 8}]))
    assert subtitle_transcript.cached_segments(None) == [speech]
    for invalid in (
        [marker],
        [speech, {**marker, "text": "spoken"}],
        [speech, {**marker, "end_seconds": 4}],
    ):
        cache.write_text(json.dumps(invalid))
        assert subtitle_transcript.cached_segments(None) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="the live embed fixture requires FFmpeg")
def test_real_embedded_ass_preserves_style(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGEIO_FFMPEG_EXE", shutil.which("ffmpeg"))
    subtitles = tmp_path / "original.ass"
    subtitles.write_text(
        "[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: KeepMe,Arial,20,&H00FFFFFF,&H000000FF,&H0,&H0,0,-1,0,0,100,100,0,0,1,2,2,2,"
        "10,10,10,1\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:03.00,KeepMe,,0,0,0,,{\\an8}Damn, close the gate.\n"
    )
    target = tmp_path / "film.mkv"
    subprocess.run(
        [
            shutil.which("ffmpeg"),
            "-v",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=32x32:d=4",
            "-i",
            str(subtitles),
            "-map",
            "0:v",
            "-map",
            "1:s",
            "-c:v",
            "ffv1",
            "-c:s",
            "copy",
            "-disposition:s:0",
            "forced",
            str(target),
        ],
        check=True,
        timeout=15,
    )
    physical = embedded_subtitle_streams(target)
    assert len(physical) == 1
    assert physical[0]["Index"] == 1
    assert physical[0]["IsForced"] is True
    source = JellyfinSubtitle(
        SubtitleSource(
            id="track",
            label="English Forced",
            language="en",
            format="ass",
            kind="embedded",
            forced=True,
        ),
        stream_index=1,
        container="ass",
        encoder="copy",
    )
    document = read_subtitle(target, source)
    redaction = redact_subtitles(document, (SubtitleMute(1.1, 1.3, "damn"),))
    output = document.render(redaction.replacements)
    assert b"Style: KeepMe,Arial" in output
    assert b"{\\an8}close the gate." in output


def test_transcript_offsets_word_and_cue_times(monkeypatch, tmp_path):
    track = AudioTrack(1, "en", None, "aac", 2, 0, 2, 50, "1/48000")
    selection = AudioTrackSelection("a" * 64, track)
    audio = ReviewedAudio(ReviewAudioSource(id="track", label="Audio"), selection, ("whisper",))
    monkeypatch.setattr(
        subtitle_transcript,
        "probe_audio_tracks",
        lambda _: SimpleNamespace(revalidate=lambda value: assert_selection(value)),
    )

    def assert_selection(value):
        assert value is selection

    monkeypatch.setattr(subtitle_transcript, "audio_playback_start", lambda *_a, **_kw: Fraction(2))
    segments = [
        {
            "start_seconds": 1,
            "end_seconds": 5,
            "text": "Hell, hell no.",
            "words": [
                {"word": "Hell,", "start_seconds": 1.5, "end_seconds": 2},
                {"word": "hell", "start_seconds": 3, "end_seconds": 3.5},
                {"word": "no.", "start_seconds": 4, "end_seconds": 4.5},
            ],
        }
    ]
    document = transcript_document(tmp_path / "film.mkv", audio, segments)
    redaction = redact_subtitles(document, (SubtitleMute(5, 5.5, "hell"),))
    assert document.cues[0].start == 3
    assert redaction.preview()[0]["filtered"] == "Hell, no."


def test_cached_transcript_rebinds_only_verified_generation_and_track(monkeypatch, tmp_path):
    media = tmp_path / "film.mkv"
    media.write_bytes(b"same content at new path")
    current = str(media)
    track = AudioTrack(1, "en", None, "aac", 2, 0, 2, 50, "1/48000")
    selection = AudioTrackSelection("a" * 64, track)
    audio = ReviewedAudio(
        ReviewAudioSource(id="track", label="Audio"),
        selection,
        ("whisper",),
        file_generation(current),
    )
    inventory = AudioTrackInventory("b" * 64, (track,))
    monkeypatch.setattr(subtitle_transcript, "probe_audio_tracks", lambda _: inventory)
    monkeypatch.setattr(subtitle_transcript, "audio_playback_start", lambda *_a, **_kw: 0)
    segments = [{"text": "hello", "start_seconds": 1, "end_seconds": 2}]
    assert len(transcript_document(media, audio, segments).cues) == 1

    changed_track = AudioTrack(1, "fr", None, "aac", 2, 0, 2, 50, "1/48000")
    inventory = AudioTrackInventory("b" * 64, (changed_track,))
    with pytest.raises(AudioSelectionError, match="audio track changed"):
        transcript_document(media, audio, segments)

    media.write_bytes(b"changed after version proof")
    with pytest.raises(StaleAudioSelectionError, match="file changed"):
        transcript_document(media, audio, segments)


def test_dot_delimited_overlapping_stems_refuse_shared_attach(tmp_path, monkeypatch):
    from scanner.integrations import jellyfin_subtitles
    from scanner.modeling.model_catalog import JellyfinMediaItem, JellyfinMediaVersion
    from scanner.runtime.subtitle_publication_names import (
        SubtitleNameError,
        require_unique_video_match,
    )

    short = tmp_path / "Movie.mkv"
    edition = tmp_path / "Movie.Alternate.mkv"
    item = JellyfinMediaItem(
        id="movie",
        item_type="Movie",
        name="Movie",
        path=str(short),
        versions=[JellyfinMediaVersion(id="edition", path=str(edition))],
    )
    monkeypatch.setattr(
        jellyfin_subtitles,
        "resolve_local_path",
        lambda _db, value: SimpleNamespace(local_path=value),
    )
    short_destination = tmp_path / "Movie.Sam - Family Review.en.srt"
    long_destination = tmp_path / "Movie.Alternate.Sam - Family Review.en.srt"
    short_siblings = jellyfin_subtitles.jellyfin_sibling_stems(None, item, short)
    long_siblings = jellyfin_subtitles.jellyfin_sibling_stems(None, item, edition)

    require_unique_video_match(short, short_destination, [], short_siblings)
    with pytest.raises(SubtitleNameError, match="more than one video"):
        require_unique_video_match(edition, long_destination, [], long_siblings)
