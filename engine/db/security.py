"""Connection policy shared by runtime and explicit administrative commands."""

import ipaddress
import os


class DatabaseConnectionError(RuntimeError):
    """A connection/configuration error safe to display without a DSN."""


def connection_options(dsn):
    """Require authenticated TLS unless every explicitly selected address is local.

    Service files and missing hosts are deliberately treated as remote: their
    settings are resolved by libpq after this check. Keyword overrides prevent
    those files or PG* environment settings from weakening the TLS policy.
    """
    from psycopg.conninfo import conninfo_to_dict

    try:
        settings = conninfo_to_dict(dsn)
    except Exception:
        raise DatabaseConnectionError("Invalid PostgreSQL connection configuration.") from None

    def effective(key):
        return settings.get(key, os.environ.get("PG" + key.upper(), ""))

    def local(value, *, sockets=False):
        if sockets and value.startswith(("/", "@")):
            return True
        # Use numeric loopback addresses, rather than trusting DNS resolution of
        # a name such as localhost, to decide whether plaintext is permitted.
        try:
            return ipaddress.ip_address(value).is_loopback
        except ValueError:
            return False

    hosts, addresses = effective("host"), effective("hostaddr")
    explicit_local = (
        not (settings.get("service") or os.environ.get("PGSERVICE"))
        and bool(hosts)
        and all(local(host, sockets=True) for host in hosts.split(","))
        and (not addresses or all(local(address) for address in addresses.split(",")))
    )
    if explicit_local:
        # Pin the checked values: PGHOSTADDR must not redirect an otherwise local
        # DSN to a remote server. An empty string disables its environment value.
        return dict(host=hosts, hostaddr=addresses)
    return dict(sslmode="verify-full", gssencmode="disable")
