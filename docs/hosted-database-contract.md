# Hosted database contract

The deployable SQL package is in `supabase/migrations/`. It is intended for the
existing shared MichaelPortfolio Supabase project, with xrBench isolated in its
own namespaces. Applying these files is an explicit deployment step; development
tests never apply them to the hosted project.

`20261027000100_xrbench_core.sql` bootstraps the current three engine schema
versions inside `xrgpu_benchmarks`. `20261027000200_xrbench_hosted_uploads.sql`
adds `xrbench_private` quota/ownership tables and the two named public RPCs below.
The package does not alter portfolio tables, Auth configuration, existing roles,
database-wide privileges, or shared `public` schema grants. Core migrations
recognize their existing schema-version rows; repeated execution preserves
reports and operator-selected limits. Review any preexisting xrBench objects
before applying to an existing deployment.

Any legacy overloads of the two named xrBench RPCs, including the earlier
four-argument ingestion function, lose EXECUTE for PUBLIC and all three API
roles. Only the reservation-bound canonical signatures are regranted to
`service_role`; unrelated portfolio functions retain their permissions.

Supabase's standard `anon`, `authenticated`, and `service_role` roles must already
exist. The migrations do not create or modify them. Only `service_role` receives
EXECUTE on these RPCs. It receives no direct access to xrBench tables. Both
functions are SECURITY DEFINER with an empty `search_path`, qualified table names,
a UTC timezone, a bounded lock wait, and explicit EXECUTE revocation from PUBLIC,
`anon`, and `authenticated`. All xrBench tables have RLS enabled and client grants
revoked. The migration owner must remain a trusted administrative role.

The edge receiver validates the trusted same-origin relay's private capability
and server-derived anonymous-browser UUID, or verifies a private client's user
through Supabase Auth. It then uses its backend secret to call these RPCs. A
browser never receives the backend secret, chooses an asserted principal UUID,
reserves its own quota or invokes report ingestion directly. Neither path changes
shared Auth settings or attests benchmark hardware. Browser cookie resets can
change the anonymous quota identity; global budgets remain enforced independently.

## Explicit deployment helper

Supply the separate administrative `XRBENCH_MIGRATION_DATABASE_URL` through your
secret manager. This helper never falls back to `XRGPU_DATABASE_URL`, local
`engine/.env`, or runtime credentials. Remote connections use the existing
verified-TLS policy and credential-free connection errors.

```sh
uv run python -m engine.hosted_deployment --check
```

The default mode is also `--check`. It runs an explicitly READ ONLY transaction
and emits a safe JSON summary. It does not apply SQL, create roles, repair grants,
reserve quota, upload a test report, or change existing data. It verifies:

- Core schema versions and RLS on the seven required xrBench tables.
- Canonical RPC signatures, exact function bodies from this release, SECURITY
  DEFINER ownership, empty search paths, and bounded lock waits.
- PUBLIC/client/API table denials and all overload permissions, including the
  legacy four-argument ingest RPC. Only canonical RPCs may grant service-role
  EXECUTE; unexpected custom-role grants also fail the audit.
- Consistent reserved-capacity accounting, operator limits, current global daily
  budget, and capacity for an 8 MiB upload's 33 MiB storage reservation.
- Shared database physical size as a metric. The summary explicitly records
  `physical_storage_headroom_verified: false`; check the provider's actual limit,
  other portfolio usage, backups and free capacity separately.

After reviewing the package and selecting the correct administrative destination,
the explicit apply mode is:

```sh
uv run python -m engine.hosted_deployment --apply
```

It applies only the two reviewed `*xrbench*.sql` files above, in sorted order and
one atomic transaction, then performs the read-only audit. Unexpected migration
filenames fail closed. It never alters shared Auth settings or unrelated schemas,
and does not deploy the website or edge function. `--apply` is an administrative
deployment action; no live project is modified by creating this package or
running its default audit. Exit 0 means this database audit passed, 1 means the
audit found a readiness failure, and 2 means missing configuration or an execution
failure. Error output never includes the administrative DSN or raw SQL errors.

## Admission reservation

```text
public.reserve_xrbench_upload(p_principal uuid, p_max_bytes bigint) -> jsonb
{
  "reservation_id": "UUID",
  "principal": "server-established anonymous-browser or verified Auth UUID",
  "max_bytes": 8388608,
  "expires_at": "UTC timestamp"
}
```

The hosted adapter chooses an 8 MiB upload cap; the database accepts a positive
cap through 32 MiB for compatibility with the generic report contract. Only the
trusted adapter chooses this value. The RPC atomically reserves one attempt and
the full requested byte cap from both per-principal and global daily budgets.
This happens before reading the request body, so schema failures and interrupted
uploads still consume their admitted attempt and byte budgets. An exhausted
budget fails without growing counters or reservations.

Default policy values are stored in the protected singleton table
`xrbench_private.upload_policy`:

| Budget | Default |
| --- | --- |
| Attempts per principal per UTC day | 20 |
| Reserved upload bytes per principal per UTC day | 256 MiB |
| Attempts across all principals per UTC day | 500 |
| Reserved upload bytes across all principals per UTC day | 4 GiB |
| Total committed plus reserved logical report storage | 256 MiB |
| Pending reservation lifetime | 5 minutes |

An administrator can update these explicit policy columns. Clients cannot set or
read policy values. Account creation does not bypass the shared global budgets.
Attempts consume the entire configured byte cap, rather than their eventual body
size; retries need fresh admission and count again. Choose production limits for
the available shared-project capacity and expected benchmark traffic.

Before reading an upload, the database reserves `4 * max_bytes + 1 MiB` of storage
capacity (33 MiB at the hosted 8 MiB cap). Expired pending reservations are removed
and their capacity released on the next successful reservation. Old daily buckets
are pruned after two preceding days; their cardinality is bounded by accepted
global attempts. Successful ingestion consumes its reservation and replaces this
pessimistic capacity with the actual accounted logical storage size.

`upload_policy` is locked first by both RPCs. This serializes budget changes and
inserts across processes and serverless instances; a process-local limiter is
not relied upon for durability or atomicity. Lock waits are limited to 3 seconds.

## Transactional ingestion

```text
public.ingest_xrbench_report(
  p_report jsonb, p_measurements jsonb,
  p_report_sha256 text, p_payload_sha256 text,
  p_principal uuid, p_reservation_id uuid
) -> jsonb
{
  "stored": true,
  "run_id": "run-...",
  "payload_sha256": "SHA-256 of this request's exact bytes",
  "report_sha256": "SHA-256 of the validated canonical report",
  "received_at": "original committed receipt timestamp",
  "verification": "schema-validated; hardware not attested"
}
```

The receiver computes both hashes after full release-aware report validation.
The database requires well-formed hashes, a valid run identity, a matching result
projection, and an unexpired reservation belonging to the verified principal.
It inserts the raw report, every result row, ownership, storage accounting, and
reservation consumption in one transaction. A failed row insert rolls the entire
operation back, including quota-capacity consumption. Its already-admitted daily
attempt remains charged because admission was a separate committed RPC.

An identical report retried by the same principal with a new reservation receives
the original report receipt and consumes no additional committed storage. The
payload hash confirms the current request bytes; equivalent JSON encoding may
have a different payload hash. An existing run belonging to another principal,
an unowned legacy run, or different report content conflicts rather than being
claimed or overwritten. A consumed reservation cannot be reused directly.

| SQLSTATE | Meaning | Receiver response |
| --- | --- | --- |
| `P0001` | Daily or storage budget exhausted | HTTP 429 |
| `23505` | Run identity/content/ownership conflict | HTTP 409 |
| `42501` | Missing, wrong-principal, expired, or consumed reservation | HTTP 503; fresh admission can retry |
| `22023` | Invalid reservation/report/projection input | Rejected; no storage success |

Only the trusted server may invoke these functions. Database validation is defense
in depth; it does not replace the edge validator's release manifest and complexity
limits or authenticate a caller-supplied UUID by itself.

## Storage and maintenance

The storage budget measures retained JSON/text values plus conservative per-run
and per-result allowances. It includes duplicated report/result/sample columns,
and checks the actual calculated size against the reservation before writing.
It is a durable application ledger, not a physical PostgreSQL disk-size meter.
WAL, indexes, TOAST overhead, dead tuples, backups, and portfolio storage consume
additional provider capacity. Check the shared project's physical storage and
headroom before deployment and monitor it independently. Do not raise xrBench's
budget simply because another section of the project is using the remaining disk.

No background job or hosted cron is needed for correctness of reservation expiry.
At the configured cap, new uploads stop. Retention or limit changes are deliberate
administrative actions. To prune old hosted reports, use one administrative
transaction that first locks the `upload_policy` row, records the selected
`upload_owners.storage_bytes`, deletes those ownership rows and reports (result
rows cascade), and subtracts exactly their accounted bytes from
`storage_used_bytes`. Do not delete rows or edit ledger columns independently.
Legacy reports without ownership records were included in the initial ledger and
need separately reviewed accounting if removed. Back up and verify restoration
before destructive maintenance.

## Verification

`engine/tests/test_hosted_database.py` is opt-in with `XRGPU_TEST_POSTGRES=1` and
requires an explicitly local administrative database. It creates UUID-named test
databases and role substitutes, applies the shipped migration files, and removes
only those temporary objects. It never restarts Docker or changes the shared
Supabase project. Tests verify a complete synthetic 221-operator, 22,100-sample
report using the production JavaScript projection, transactional rollback,
ownership/conflicts/retries, expired and replayed reservations, byte and attempt
quotas under concurrent connections, total storage capacity, anonymous/client
access denial, migration repetition, and an unchanged portfolio sentinel/grant.

References: [Supabase function security](https://supabase.com/docs/guides/database/functions),
[Supabase grants and RLS](https://supabase.com/docs/guides/database/postgres/row-level-security),
and [PostgreSQL row locks](https://www.postgresql.org/docs/17/explicit-locking.html).
