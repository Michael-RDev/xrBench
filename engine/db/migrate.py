"""Explicit administrative schema setup, separate from hosted runtime credentials."""

import os

from engine.db import Store


def main():
    dsn = os.environ.get("XRGPU_MIGRATION_DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "Set XRGPU_MIGRATION_DATABASE_URL to an administrative connection, then rerun. "
            "See docs/database-hosting.md."
        )
    try:
        store = Store(dsn)
        store.migrate()
        store.health()
    except Exception:
        raise SystemExit(
            "Database migration failed. Check administrative permissions, verified TLS and "
            "engine/db/migrations/. Existing reports are preserved."
        ) from None
    print("PostgreSQL schema is ready.")


if __name__ == "__main__":
    main()
