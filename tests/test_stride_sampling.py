"""Strided sample-and-hold scoring with bisected boundaries."""

import math

import pytest

from scanner.pipeline.stride_sampling import sample_and_hold


def _run(labels, stride):
    calls = []

    def score(index):
        calls.append(index)
        return (index, labels[index])

    results = list(sample_and_hold(len(labels), stride, score, lambda result: result[1]))
    return results, calls


def _assert_boundaries_exact(labels, results):
    assert len(results) == len(labels)
    assert [label for _index, label in results] == labels


def test_uniform_run_scores_only_strided_anchors_and_holds_nearest():
    labels = ["clean"] * 10
    results, calls = _run(labels, 4)

    assert calls == [0, 4, 8, 9]
    assert [index for index, _label in results] == [0, 0, 0, 4, 4, 4, 4, 8, 8, 9]


def test_single_transition_is_located_to_adjacent_frames():
    labels = ["clean"] * 13 + ["nudity"] * 12
    results, calls = _run(labels, 8)

    _assert_boundaries_exact(labels, results)
    assert {12, 13} <= set(calls)
    assert len(calls) <= 5 + math.ceil(math.log2(8))


def test_multiple_transitions_across_and_within_gaps_are_resolved():
    labels = ["a"] * 5 + ["b"] * 6 + ["a"] * 3 + ["c"] * 10
    results, _calls = _run(labels, 6)

    _assert_boundaries_exact(labels, results)


def test_held_frames_never_cross_a_located_boundary():
    labels = ["clean"] * 7 + ["violence"] * 9
    results, _calls = _run(labels, 5)

    for position, (source, label) in enumerate(results):
        assert labels[source] == label == labels[position]


def test_stride_one_scores_every_frame_once():
    labels = ["a", "b", "b", "a"]
    results, calls = _run(labels, 1)

    assert calls == [0, 1, 2, 3]
    assert [index for index, _label in results] == [0, 1, 2, 3]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_short_lists_always_score_first_and_last(count):
    labels = ["x"] * count
    results, calls = _run(labels, 12)

    assert calls == list(range(count))
    assert len(results) == count


def test_first_and_last_frames_are_scored_even_off_stride():
    labels = ["a"] * 9 + ["b"]
    results, calls = _run(labels, 4)

    assert calls[:3] == [0, 4, 8]
    assert 9 in calls
    _assert_boundaries_exact(labels, results)


def test_scoring_calls_are_bounded_by_anchors_plus_log_refinement():
    stride = 12
    labels = (["a"] * 30 + ["b"] * 30) * 4
    results, calls = _run(labels, stride)

    anchors = math.ceil((len(labels) - 1) / stride) + 1
    transitions = sum(1 for left, right in zip(labels, labels[1:]) if left != right)
    assert len(calls) == len(set(calls))
    assert len(calls) <= anchors + transitions * math.ceil(math.log2(stride))
    assert len(calls) < len(labels) / 4
    _assert_boundaries_exact(labels, results)


def test_results_stream_before_later_frames_are_scored():
    calls = []

    def score(index):
        calls.append(index)
        return index

    stream = sample_and_hold(100, 10, score, lambda _result: "same")
    assert next(stream) == 0
    assert max(calls) == 10
