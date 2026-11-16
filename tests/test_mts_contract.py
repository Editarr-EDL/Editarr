import json
import math
from pathlib import Path

from scanner.models import McfImportOptions, ReviewRetimeRequest, ReviewRetimeResult

CONTRACT_PATH = Path(__file__).parent / "fixtures" / "mts" / "contract-v1.json"


def _map_endpoint(endpoint: float, source: list[float], target: list[float]) -> float:
    return target[0] + ((endpoint - source[0]) / (source[1] - source[0])) * (
        target[1] - target[0]
    )


def _map_range(value: list[float], source: list[float], target: list[float]) -> list[float]:
    return [_map_endpoint(endpoint, source, target) for endpoint in value]


def test_mts_affine_vectors_freeze_full_precision_and_repeated_current_basis():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    for vector in contract["affine_vectors"]:
        actual = [
            _map_range(value, vector["source"], vector["target"])
            for value in vector["ranges"]
        ]
        assert len(actual) == len(vector["expected"])
        for actual_range, expected_range in zip(actual, vector["expected"], strict=True):
            assert actual_range == expected_range or all(
                math.isclose(actual_value, expected_value, rel_tol=0, abs_tol=1e-12)
                for actual_value, expected_value in zip(
                    actual_range, expected_range, strict=True
                )
            )

    repeated = contract["repeated_mapping"]
    current = repeated["initial_range"]
    for step in repeated["steps"]:
        current = _map_range(current, step["source"], step["target"])
        assert current == step["expected"]


def test_mts_contract_freezes_cross_layer_names_and_ordering():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["version"] == "mts-contract-v1"
    assert contract["enums"]["SynchronizationEndMode"] == [
        "before_credits",
        "file_end",
    ]
    assert contract["enums"]["ScanMode"] == ["content", "credits_detection"]
    assert contract["validation_order"] == [
        "finite_numbers",
        "source_interval",
        "target_interval",
        "authoritative_runtime",
        "input_ranges",
        "map_endpoints",
        "finite_results",
        "mapped_ranges_and_runtime",
    ]
    assert contract["fatal_source_bounds"] == [
        "missing",
        "malformed",
        "equal",
        "reversed",
        "recovered",
    ]
    assert contract["models"]["ReviewSynchronization"] == [
        "start_seconds",
        "end_seconds",
        "end_mode",
    ]
    assert contract["models"]["McfImportOptions"][-3:] == [
        "synchronization",
        "mcf_material_end_seconds",
        "accepted_credits",
    ]
    assert list(McfImportOptions.model_fields) == contract["models"]["McfImportOptions"]
    expected_request = contract["models"]["ReviewRetimeRequest"].copy()
    expected_request.insert(expected_request.index("accepted_credits"), "filter_basis")
    assert list(ReviewRetimeRequest.model_fields) == [
        "policy_owner_id",
        "policy_revision",
        *expected_request,
    ]
    expected_result = contract["models"]["ReviewRetimeResult"].copy()
    expected_result.insert(expected_result.index("accepted_credits"), "base_filters")
    assert list(ReviewRetimeResult.model_fields) == [
        *expected_result,
        "policy_owner_id",
        "policy_revision",
    ]
    assert McfImportOptions.model_fields["synchronization"].is_required()
    assert McfImportOptions.model_fields["ignore_errors"].default is False
    assert contract["typescript_unions"]["CreditsDetectionResult"] == {
        "discriminator": "outcome",
        "values": ["candidate", "no_candidate"],
    }
    assert contract["confidence_band_intervals"] == (
        "low:[0,.6);medium:[.6,.8);high:[.8,1];unrounded"
    )

    examples = contract["wire_examples"]
    assert list(examples["mcf_import_options"]) == contract["models"]["McfImportOptions"]
    assert list(examples["review_retime_request"]) == contract["models"][
        "ReviewRetimeRequest"
    ]
    ReviewRetimeRequest.model_validate(examples["review_retime_request"])
    assert examples["candidate_result"]["best_candidate_id"] == "candidate-1"
    assert examples["candidate_result"]["candidates"][0]["confidence_band"] == "high"
    assert examples["low_confidence_result"]["candidates"][0]["confidence_band"] == "low"
    assert examples["no_candidate_result"] == {
        "outcome": "no_candidate",
        "candidates": [],
        "best_candidate_id": None,
    }


def test_mts_contract_freezes_routes_queue_and_bounded_clip():
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["routes"] == [
        ["POST", "/api/reviews/{review_id}/retime", 200],
        ["POST", "/api/credits-detections", 202],
        ["GET", "/api/credits-detections/{scan_id}", 200],
        ["POST", "/api/credits-detections/{scan_id}/model-consent", 202],
        ["POST", "/api/credits-detections/{scan_id}/accept", 200],
        ["GET", "/api/credits-detections/{scan_id}/clip", 200],
        ["DELETE", "/api/scans/queue/{queue_id}", 200],
    ]
    assert contract["queue_transitions"]["awaiting_model_consent"] == [
        "acquiring_model",
        "queued",
        "cancelled",
    ]
    assert contract["queue_id_behavior"] == (
        "created_before_start_response_including_awaiting_model_consent"
    )
    assert contract["clip"] == {
        "mime_type": "video/mp4",
        "maximum_duration_seconds": 12.0,
        "maximum_size_bytes": 16 * 1024 * 1024,
        "content_disposition": 'inline; filename="credits-preview.mp4"',
        "cache_control": "no-store",
    }
