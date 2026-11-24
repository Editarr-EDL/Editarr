"""Tests of the revision protocol that migration 0037 retires (ADR 0002, D2).

0037 limits `community_operations` to `publish`, `submission` and `filter_vote`, so
revision receipts and votes can no longer be queued. These tests must fail until
the master-set integration removes that flow; strict xfail flags any that pass.
"""

import pytest

RETIRED_REVISION_PROTOCOL = pytest.mark.xfail(
    strict=True,
    reason="Migration 0037 retired revision receipts and votes (ADR 0002 D2)",
)
