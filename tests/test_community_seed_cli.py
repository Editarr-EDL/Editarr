from scanner.commands.community_seed_cli import prepare_source


def test_seed_builder_preserves_provider_numbering_and_reports_incomplete_cues():
    cue = {"start_seconds": 0.1235, "end_seconds": 2.5, "line_number": 5,
           "category": "language", "subcategory": "swearing", "severity": "medium", "edit_action": "mute"}
    record = {
        "id": "videoskip:123",
        "attribution": {"title": "A Show S1 E1: Short (2020)", "kind": "episode", "source_url": "https://example.test"},
        "sync": {"reference_seconds": 2.0},
        "filters": [cue, {**cue, "line_number": 6, "severity": None}],
    }
    match = {"tmdb": "603", "season": 0, "episode": 9, "runtime_ms": 60_000}
    bundle, report = prepare_source("VideoSkip", [record], {record["id"]: match}, {"language": ["swearing"]})
    [seed] = bundle["records"]
    assert seed["work"]["season"] == 0
    assert seed["work"]["episode"] == 9
    assert seed["sourceRuntimeMs"] is None
    assert seed["provenance"]["sync"] == record["sync"]
    assert seed["filters"] == [{
        "startMs": 124, "endMs": 2500, "category": "language", "subcategory": "swearing",
        "severity": "medium", "action": "mute", "sourceCueIndex": 5,
    }]
    assert report["review"][0]["issues"] == [{"sourceCueIndex": 6, "reason": "incomplete_or_unknown_classification"}]
