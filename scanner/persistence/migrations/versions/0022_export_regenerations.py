"""Move personal export regeneration work from snapshot JSON into an indexed table."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0022_export_regenerations"
down_revision = "0021_export_snapshots"
branch_labels = None
depends_on = None

_DUE = {"queued", "running"}


def _number(value, name, export_id):
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValueError(f"Unsupported regeneration {name}: export={export_id}")
    return value


def _job(export_id, job, serving):
    """Translate one JSON job exactly; refuse anything the 0021 writer could not produce."""
    state = job.get("state")
    if state not in {"queued", "running", "succeeded", "failed"}:
        raise ValueError(f"Unsupported regeneration state: export={export_id}")
    owner = job.get("policy_owner_id")
    if owner is not None and (isinstance(owner, bool) or not isinstance(owner, int)):
        raise ValueError(f"Unsupported regeneration owner: export={export_id}")
    revision = _number(job.get("policy_revision"), "policy revision", export_id)
    attempts = _number(job.get("attempts", 0), "attempts", export_id)
    if not isinstance(revision, int) or not isinstance(attempts, int):
        raise ValueError(f"Unsupported regeneration counter: export={export_id}")
    last = job.get("last_attempt")
    token = job.get("token") if state == "running" else None
    if state == "running" and not isinstance(token, str):
        raise ValueError(f"Unsupported regeneration claim: export={export_id}")
    reason = None
    if state == "failed":
        state, reason = (
            ("failed", "retries_exhausted") if serving else ("withdrawn", "regeneration_failed")
        )
    return {
        "export_id": export_id,
        "state": state,
        "due_at": (
            _number(job.get("next_attempt_at", 0), "due time", export_id) if state in _DUE else None
        ),
        "attempts": attempts,
        "last_attempt_at": (None if last is None else _number(last, "attempt time", export_id)),
        "policy_owner_id": owner,
        "policy_revision": revision,
        "claim_token": token,
        "failure_reason": reason,
    }


def upgrade():
    op.create_table(
        "media_item_export_regenerations",
        sa.Column(
            "export_id",
            sa.Integer(),
            sa.ForeignKey("media_item_exports.id", ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("due_at", sa.Float(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.Float(), nullable=True),
        sa.Column("policy_owner_id", sa.Integer(), nullable=True),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'withdrawing', 'succeeded', 'failed', 'withdrawn')",
            name="ck_export_regenerations_state",
        ),
        sa.CheckConstraint(
            "(state IN ('queued', 'running', 'withdrawing')) = (due_at IS NOT NULL)",
            name="ck_export_regenerations_due",
        ),
        sa.CheckConstraint(
            "(state IN ('withdrawing', 'failed', 'withdrawn')) = (failure_reason IS NOT NULL) "
            "AND (failure_reason IS NULL "
            "OR failure_reason IN ('regeneration_failed', 'retries_exhausted', 'deleted')) "
            "AND (failure_reason IS NOT 'deleted' OR state = 'withdrawn')",
            name="ck_export_regenerations_failure",
        ),
        sa.CheckConstraint(
            "state != 'running' OR claim_token IS NOT NULL",
            name="ck_export_regenerations_claim",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND policy_revision >= 0",
            name="ck_export_regenerations_counts",
        ),
    )
    op.create_index(
        "ix_export_regenerations_due",
        "media_item_export_regenerations",
        ["due_at", "export_id"],
        sqlite_where=sa.text("due_at IS NOT NULL"),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, scope, content_bytes IS NOT NULL, snapshot_json "
            "FROM media_item_exports WHERE snapshot_json IS NOT NULL ORDER BY id"
        )
    ).all()
    for export_id, scope, serving, document in rows:
        snapshot = json.loads(document)
        job = snapshot.pop("regeneration", None)
        recovery = snapshot.pop("recovery_required", None)
        if job is None and recovery is None:
            continue
        if job is not None:
            if scope != "personal" or not isinstance(job, dict):
                raise ValueError(f"Unsupported regeneration job: export={export_id}")
            connection.execute(
                sa.text(
                    "INSERT INTO media_item_export_regenerations (export_id, state, due_at, "
                    "attempts, last_attempt_at, policy_owner_id, policy_revision, claim_token, "
                    "failure_reason) VALUES (:export_id, :state, :due_at, :attempts, "
                    ":last_attempt_at, :policy_owner_id, :policy_revision, :claim_token, "
                    ":failure_reason)"
                ),
                _job(export_id, job, bool(serving)),
            )
        connection.execute(
            sa.text("UPDATE media_item_exports SET snapshot_json = :document WHERE id = :id"),
            {
                "id": export_id,
                "document": json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
            },
        )


def downgrade():
    raise RuntimeError("Export regeneration work requires backup recovery, not a downgrade")
