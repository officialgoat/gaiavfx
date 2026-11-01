from alembic import context
from sqlalchemy import create_engine, event

engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
if context.config.attributes.get("staging"):
    # A library being created is a throwaway file until LibraryService renames it into place, so its migrations need
    # not wait on the disk for every statement; the creator syncs the finished file once.
    @event.listens_for(engine, "connect")
    def _unsynced(connection, _record):
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute("PRAGMA synchronous=OFF")


try:
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
