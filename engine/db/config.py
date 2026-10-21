"""Read database connection settings without creating files or starting services."""
import os

from engine.db import Store
from engine.paths import ENV


class DatabaseStartupError(RuntimeError):
    """An actionable startup failure that does not expose database credentials."""


def database():
    from psycopg.conninfo import make_conninfo
    if os.environ.get("XRGPU_DATABASE_URL"):
        return Store(os.environ["XRGPU_DATABASE_URL"])
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator:
                raise DatabaseStartupError("Invalid engine/.env. Use engine/.env.example as a template.")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('\"', "'"):
                value = value[1:-1]
            env[key.strip()] = value
    for key in ('XRGPU_DB_PASSWORD', 'XRGPU_DB_PORT'):
        if key in os.environ:
            env[key] = os.environ[key]
    password = env.get('XRGPU_DB_PASSWORD', '')
    if not password or password == 'replace-with-your-own-password':
        raise DatabaseStartupError(
            "Configure engine/.env from engine/.env.example and set XRGPU_DB_PASSWORD, "
            "or set XRGPU_DATABASE_URL. See engine/README.md."
        )
    try:
        port = int(env.get('XRGPU_DB_PORT', '5432'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise DatabaseStartupError("XRGPU_DB_PORT must be a port number between 1 and 65535.") from None
    return Store(make_conninfo(host="127.0.0.1", port=port, dbname="xrgpu", user="xrgpu", password=password))
