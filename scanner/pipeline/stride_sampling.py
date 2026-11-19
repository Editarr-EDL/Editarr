"""Strided frame scoring with bisected boundaries and sample-and-hold fill."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from typing import TypeVar

T = TypeVar("T")


def sample_and_hold(
    count: int,
    stride: int,
    score: Callable[[int], T],
    key: Callable[[T], Hashable],
) -> Iterator[T]:
    """Yield one result per index in ``range(count)`` while scoring only a subset.

    Indices are scored every ``stride`` frames, always including the first and last.
    When two consecutive scored indices have different ``key`` values, the gap is
    bisected until the change is located between adjacent indices. Every unscored
    index then lies inside a uniform run and takes the result of its nearest scored
    neighbour (the earlier one on ties). Results are yielded lazily in index order as
    each strided gap is resolved, so callers can report progress and stop early.
    A change that starts and ends between two scored indices with equal keys is not
    detected; the stride bounds the shortest event that is guaranteed to be seen.
    """
    if count <= 0:
        return
    stride = max(1, int(stride))
    anchors = list(range(0, count, stride))
    if anchors[-1] != count - 1:
        anchors.append(count - 1)

    scored: dict[int, T] = {}

    def result(index: int) -> T:
        if index not in scored:
            scored[index] = score(index)
        return scored[index]

    def refine(left: int, right: int) -> None:
        pending = [(left, right)]
        while pending:
            low, high = pending.pop()
            if high - low <= 1 or key(result(low)) == key(result(high)):
                continue
            middle = (low + high) // 2
            result(middle)
            pending.extend(((middle, high), (low, middle)))

    previous = anchors[0]
    result(previous)
    for anchor in anchors[1:]:
        result(anchor)
        refine(previous, anchor)
        points = sorted(index for index in scored if previous <= index <= anchor)
        for low, high in zip(points, points[1:]):
            for index in range(low, high):
                yield scored[low] if index - low <= high - index else scored[high]
        for index in points[:-1]:
            del scored[index]
        previous = anchor
    yield scored[previous]
