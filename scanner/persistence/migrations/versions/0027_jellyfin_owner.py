"""Let the owner be a Jellyfin-linked account without a local password.

First-run setup now creates the owner from a Jellyfin administrator sign-in, so
`ck_users_owner_state` keeps only the rule that the owner is active. Every row
stays as it is, and so does the `users` AUTOINCREMENT high-water mark, which
rebuilding the table would otherwise reset.
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_jellyfin_owner"
down_revision = "0026_archive_manual_files"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    sequence = bind.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = 'users'"))
    high_water = sequence.scalar()
    with op.batch_alter_table(
        "users", recreate="always", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_constraint("ck_users_owner_state", type_="check")
        batch.create_check_constraint(
            "ck_users_owner_state", "role != 'owner' OR status = 'active'"
        )
    if high_water is not None:
        bind.execute(sa.text("DELETE FROM sqlite_sequence WHERE name = 'users'"))
        bind.execute(
            sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES ('users', :seq)"),
            {"seq": high_water},
        )


def downgrade():
    raise RuntimeError("Local migrations cannot require a local password for a Jellyfin owner")
