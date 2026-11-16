"""Give each API key a wrapped copy of its value instead of stored plaintext.

Adds one nullable column; no existing row changes here. A migration never sees
the live data directory (a restore migrates a staged copy), so the application
seals each legacy plaintext value at startup and after restore activation. The
check keeps a row from holding plaintext and its wrapped copy at once.
"""

from alembic import op

revision = "0015_sealed_api_keys"
down_revision = "0014_media_access_exceptions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE api_keys ADD COLUMN secret_envelope TEXT "
        "CONSTRAINT ck_api_keys_one_secret CHECK (token_value IS NULL OR secret_envelope IS NULL)"
    )


def downgrade():
    raise RuntimeError("Local migrations cannot discard wrapped API keys")
