"""Run local migrations only on an explicitly supplied SQLite connection."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("A coordinated SQLite connection is required for local migrations")
if connection.dialect.name != "sqlite":
    raise RuntimeError("Local migrations require SQLite")

context.configure(connection=connection, transactional_ddl=False)
with context.begin_transaction():
    context.run_migrations()
