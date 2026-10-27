# Hosted database setup

Use a dedicated PostgreSQL database on a private network with firewall access
limited to the receiver. Keep database credentials and administrative keys on
the server. The browser should contact an authenticated, rate-limited report
receiver over HTTPS, rather than connect to PostgreSQL itself.

The repository's Python HTTP servers are development tools bound to loopback.
Keep the developer service, device controls, state directories and PostgreSQL
port private. Local Compose credentials belong to its development database; its
Docker-created `xrgpu` account is a superuser and must not become the hosted
application account. This setup does not change existing Docker volumes or roles.

## Verified transport

Set `XRGPU_DATABASE_URL` from your server's secret manager with the provider's
hostname and a restricted runtime role. For example, the connection parameters
can include `host=db.example.internal dbname=xrbench user=xrbench_runtime
sslmode=verify-full sslrootcert=/run/secrets/postgres-ca.crt` plus the password
supplied through protected secrets. Use your provider's actual trusted CA, or
`sslrootcert=system` when supported by the installed libpq/provider.

`Store.connect()` forces `sslmode=verify-full` for remote or ambiguous addresses,
even if a DSN, service file or `PGSSLMODE` requests a weaker mode. It disables GSS
negotiation on those connections so TLS certificate verification is always used.
Existing CA/client-certificate parameters are retained. Missing CA trust or a
hostname/certificate mismatch fails the connection. Plaintext is permitted only
when every explicitly configured host and host address is a numeric loopback
address or Unix socket; names such as `localhost`, missing hosts, mixed-host
lists and service configurations use the verified-TLS policy. Prefer `127.0.0.1`
or `::1` for local TCP development.

## Separate schema setup from runtime

External startup performs connectivity and schema checks only. It never applies
migrations through `XRGPU_DATABASE_URL`. Before starting an external deployment:

1. Supply `XRGPU_MIGRATION_DATABASE_URL` to a separate administrative job with
   schema-owner credentials and verified transport. Run
   `uv run python -m engine.db.migrate` to apply the versioned SQL and check it.
   Remove this secret from the runtime environment after the job finishes.
2. As an administrator in that dedicated database, use psql's `\i
   engine/db/runtime-role.sql` to create `xrbench_runtime` and enter its password
   through the prompt. Configure the psql connection with verified TLS as well.
   The script deliberately refuses an existing role; inspect its ownership,
   attributes, memberships and grants before reusing or replacing it.
3. Give the application only the runtime role's connection secret. It has schema
   usage, SELECT on migration versions, and SELECT/INSERT on the two report
   tables. It has no DDL, UPDATE, DELETE, owner, superuser, role creation or role
   membership privileges. Revisit grants explicitly when future migrations add
   tables. Keep administrative credentials out of the deployed process.
4. Verify with the runtime credentials that schema health checks and a report
   insertion/retry work, and that ALTER/DROP/DELETE fail. Make a backup and verify
   restoration before accepting public uploads. Apply retention and storage
   quotas at the receiver/database deployment.

The SQL role script is an explicit administrative action; ordinary application
startup does not create roles, revoke existing permissions or alter saved data.
This role can read all reports and insert new ones. If multiple users need
isolated report access, implement authorization and row-level policies in the
hosted service before exposing reads.

## Hosted receiver gate

The Supabase schema/RPC, durable quotas and authenticated edge entrypoint are now
implemented. Follow the [deployment handoff](deployment-readiness.md) and
[hosted database contract](hosted-database-contract.md) for that path. Its RPC-only
service role differs from the direct Python runtime role described above: keep
the two deployment options' grants separate. The read-only hosted audit is
`python -m engine.hosted_deployment --check`; applying its two scoped migrations
requires the explicit `--apply` action and a separate administrative secret.

Service-role keys remain server-only. An allowed Origin and a valid native report
schema do not authenticate a submitter or attest that hardware ran the report.

See [PostgreSQL certificate verification](https://www.postgresql.org/docs/17/libpq-ssl.html)
and [role privileges](https://www.postgresql.org/docs/17/role-attributes.html).
