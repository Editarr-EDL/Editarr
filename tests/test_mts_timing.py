import json
import math
from pathlib import Path
from uuid import uuid4

import pytest

from scanner.mcf import (
    TimingInterval,
    map_affine_endpoint,
    map_affine_entries,
    map_affine_range,
)
from scanner.models import NewManualReviewFilter, ReviewFilterInput

CONTRACT_PATH = Path(__file__).parent / "fixtures" / "mts" / "contract-v1.json"


def test_affine_contract_vectors_and_repeated_current_basis():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for vector in contract["affine_vectors"]:
        source = TimingInterval(*vector["source"])
        target = TimingInterval(*vector["target"])
        actual = [map_affine_range(*value, source, target) for value in vector["ranges"]]
        for actual_range, expected_range in zip(actual, vector["expected"], strict=True):
            assert actual_range == pytest.approx(expected_range, abs=1e-12)

    repeated = contract["repeated_mapping"]
    current = tuple(repeated["initial_range"])
    for step in repeated["steps"]:
        current = map_affine_range(
            *current, TimingInterval(*step["source"]), TimingInterval(*step["target"])
        )
        assert current == pytest.approx(step["expected"], abs=1e-12)


def test_affine_mapping_preserves_entry_order_type_and_non_timing_fields():
    linked = ReviewFilterInput(
        id=str(uuid4()), source_scan_id="scan", source_finding_id=1,
        source="visual", category="dispensable_scenes", subcategory="dispensable",
        severity="high", mcf_channel="video", mcf_comment="End Credits",
        label="Linked", details="Details", group_key="group", start_seconds=20,
        end_seconds=40, confidence=0.75, edit_action="skip", selected=False,
    )
    manual = NewManualReviewFilter(
        id=str(uuid4()), source="audio", category="language", subcategory="swearing",
        severity="medium", mcf_channel="audio", mcf_comment="Word", label="Manual",
        details=None, group_key="manual", start_seconds=20, end_seconds=40,
        confidence=1, edit_action="mute", selected=True,
    )

    linked_result = map_affine_entries(
        [linked, linked.model_copy(update={"id": str(uuid4())})],
        TimingInterval(0, 100),
        TimingInterval(10, 210),
    )
    manual_result = map_affine_entries(
        [manual], TimingInterval(0, 100), TimingInterval(10, 210)
    )

    assert [entry.start_seconds for entry in linked_result] == [50, 50]
    assert [entry.end_seconds for entry in linked_result] == [90, 90]
    assert linked_result[0].model_dump(exclude={"start_seconds", "end_seconds"}) == (
        linked.model_dump(exclude={"start_seconds", "end_seconds"})
    )
    assert isinstance(linked_result[0], ReviewFilterInput)
    assert isinstance(manual_result[0], NewManualReviewFilter)
    assert manual_result[0].start_seconds == 50
    assert manual_result[0].end_seconds == 90


@pytest.mark.parametrize(
    ("source", "target", "cue", "duration", "message"),
    [
        (TimingInterval(0, math.inf), TimingInterval(0, 1), (0, 1), None, "Source"),
        (TimingInterval(-1, 1), TimingInterval(0, 1), (0, 1), None, "Source"),
        (TimingInterval(1, 1), TimingInterval(0, 1), (0, 1), None, "Source"),
        (TimingInterval(0, 1), TimingInterval(-1, 1), (0, 1), None, "Target"),
        (TimingInterval(0, 1), TimingInterval(2, 1), (0, 1), None, "Target"),
        (TimingInterval(0, 1), TimingInterval(0, 2), (0, 1), 1, "runtime"),
        (TimingInterval(0, 1), TimingInterval(0, 1), (0, 1), math.nan, "runtime"),
        (TimingInterval(0, 1), TimingInterval(0, 1), (-1, 1), None, "Cue range"),
        (TimingInterval(0, 1), TimingInterval(0, 1), (1, 1), None, "Cue range"),
        (TimingInterval(1, 2), TimingInterval(0, 1), (0, 1), None, "outside"),
        (
            TimingInterval(0, 1e-308),
            TimingInterval(0, 1e308),
            (1, 2),
            None,
            "finite",
        ),
    ],
)
def test_affine_mapping_rejects_invalid_intervals_ranges_and_results(
    source, target, cue, duration, message
):
    with pytest.raises(ValueError, match=message):
        map_affine_range(*cue, source, target, duration_seconds=duration)


def test_affine_mapping_maps_null_end_and_rejects_non_finite_endpoint():
    assert map_affine_range(
        25, None, TimingInterval(0, 100), TimingInterval(10, 210)
    ) == (60, None)
    with pytest.raises(ValueError, match="finite"):
        map_affine_endpoint(
            math.nan, TimingInterval(0, 100), TimingInterval(0, 100)
        )
    with pytest.raises(ValueError, match="finite"):
        map_affine_endpoint(
            "1", TimingInterval(0, 100), TimingInterval(0, 100)  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="finite"):
        map_affine_endpoint(
            True, TimingInterval(0, 100), TimingInterval(0, 100)
        )
