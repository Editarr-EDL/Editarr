"""Subtitle redaction keeps exact source styling and conservative matches."""

from dataclasses import replace

from scanner.formats.subtitle_redaction import SubtitleMute, redact_subtitles
from scanner.formats.subtitles import parse_subtitles


def test_srt_redacts_unique_words_only_and_removes_empty_cues():
    content = (
        b"1\r\n00:00:01,000 --> 00:00:04,000\r\nGet <i>the hell</i> out.\r\n\r\n"
        b"2\r\n00:00:04,000 --> 00:00:05,000\r\nHello, hello.\r\n\r\n"
        b"3\r\n00:00:06,000 --> 00:00:07,000\r\nDamn!\r\n"
    )
    mutes = (
        SubtitleMute(1, 2, "tHe HeLl"),
        SubtitleMute(4, 5, "hello"),
        SubtitleMute(6, 7, "damn"),
    )
    redaction = redact_subtitles(parse_subtitles(content, "srt"), mutes)
    assert redaction.document.render(redaction.replacements) == (
        b"1\r\n00:00:01,000 --> 00:00:04,000\r\nGet <i></i>out.\r\n\r\n"
        b"2\r\n00:00:04,000 --> 00:00:05,000\r\nHello, hello.\r\n\r\n"
    )
    assert redaction.preview()[0] == {
        "start_seconds": 1,
        "end_seconds": 4,
        "original": "Get the hell out.",
        "filtered": "Get out.",
    }


def test_styled_ass_and_vtt_keep_unmodified_headers_and_positioning():
    prefix = (
        "[Script Info]\nTitle: Original\n[V4+ Styles]\nStyle: KeepMe,Arial\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:00:04.00,KeepMe,name,0,0,0,,"
    )
    original = (prefix + r"{\an8}Well, {\i1}damn{\i0}, fine." + "\n").encode()
    redaction = redact_subtitles(parse_subtitles(original, "ass"), (SubtitleMute(2, 3, "damn"),))
    assert (
        redaction.document.render(redaction.replacements)
        == (prefix + r"{\an8}Well, {\i1}{\i0}fine." + "\n").encode()
    )

    content = (
        b"WEBVTT\n\nSTYLE\n::cue { color: red; }\n\n"
        b"cue-1\n00:01.000 --> 00:04.000 line:90%\nDamn, go.\n"
    )
    redaction = redact_subtitles(parse_subtitles(content, "vtt"), (SubtitleMute(2, 3, "damn"),))
    assert redaction.document.render(redaction.replacements) == content.replace(b"Damn, ", b"")


def test_transcript_word_timing_identifies_a_repeated_expression():
    document = parse_subtitles(b"1\n00:00:01,000 --> 00:00:05,000\nHell, hell no.\n", "srt")
    cue = replace(document.cues[0], words=(("Hell,", 1.5, 2), ("hell", 3, 3.5), ("no", 4, 4.5)))
    timed = replace(document, cues=(cue,))
    redaction = redact_subtitles(timed, (SubtitleMute(3, 3.5, "hell"),))
    assert redaction.preview()[0]["filtered"] == "Hell, no."


def test_phrase_does_not_cross_punctuation_or_speaker_break():
    original = b"1\n00:00:01,000 --> 00:00:06,000\n- Hell.\n- You okay?\n"
    document = parse_subtitles(original, "srt")
    output = redact_subtitles(document, (SubtitleMute(1, 6, "hell you"),))
    assert output.document.render(output.replacements) == original


def test_a_whole_cue_mute_removes_every_cue_it_overlaps():
    content = (
        b"1\r\n00:00:01,000 --> 00:00:02,000\r\nKeep me.\r\n\r\n"
        b"2\r\n00:00:03,000 --> 00:00:04,000\r\nA word nobody named.\r\n\r\n"
        b"3\r\n00:00:04,500 --> 00:00:05,000\r\nAnd me too.\r\n"
    )
    mutes = (SubtitleMute(3.5, 4.6, "", whole_cue=True),)
    redaction = redact_subtitles(parse_subtitles(content, "srt"), mutes)
    assert redaction.document.render(redaction.replacements) == (
        b"1\r\n00:00:01,000 --> 00:00:02,000\r\nKeep me.\r\n\r\n"
    )
