"""Shared fixtures for the folder evaluation harness tests."""

from __future__ import annotations

from scanner.evaluation.folder_eval.inventory import plan_frame_times


def clip(identifier, folder, expected, frames=4, duration=8.0, scored=True, kind="video"):
    times = plan_frame_times(kind, duration)[:frames]
    return {
        "id": identifier, "path": f"{folder}/{identifier}.mp4", "folder": folder, "kind": kind,
        "duration_s": duration, "expected": expected, "scored": scored,
        "frames": [{"n": n, "t": t, "file": f"frames/{identifier}/{n}.jpg"}
                   for n, t in enumerate(times)],
    }
