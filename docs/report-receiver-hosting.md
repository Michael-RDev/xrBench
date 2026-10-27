# Protected report receiver

The active public flow automatically uploads completed results through the
same-origin `/api/reports` relay and keeps JSON downloads available. Visitors
enter no email, create no benchmark account and see no sign-in step. Browser
evidence stays local while uploads retry; a receiver failure cannot rerun a
benchmark or remove its results. CSP restricts browser connections to the site's
own origin. The relay is deployed and verified through actual HTTP receipts and
PostgreSQL rows; see the [activation evidence](deployment-readiness.md).

Database submission remains a protected server capability. Its implementation
is `server/supabase-adapter.mjs`, with the deployable Deno entrypoint in
`supabase/functions/submit-report/index.ts` and scoped SQL in
`supabase/migrations/`. Follow the [deployment handoff](deployment-readiness.md)
for current activation records, build/install and maintenance commands.

## Authentication and durable admission

The public browser calls only its own Worker. That Worker establishes a signed
Secure/HttpOnly/SameSite=Strict `__Host-xrbench-upload` cookie before a report can
commit, then derives a stable anonymous-browser UUID from it. It strips client
identity/proxy headers and calls the edge receiver with a private capability in
`x-xrbench-proxy-key` and that UUID in `x-xrbench-principal`. The edge verifies the
32-byte base64url capability with WebCrypto HMAC verification; bad, partial or
disabled proxy assertions fail closed without falling back to another identity.

Gateway JWT verification stays enabled. The relay supplies the project's legacy
anon JWT for that gateway check; the JWT alone does not authorize ingestion.
The compatible private-client path can still supply a real Supabase user access
token, which the receiver verifies through `/auth/v1/user`. Neither flow sends
email, creates accounts or changes global Auth settings. A forged Origin, public
key, decoded token payload or visitor-chosen UUID is insufficient.

Before reading a body, the adapter calls `reserve_xrbench_upload` with the
server-established UUID. PostgreSQL atomically checks per-principal and global daily
attempt/byte budgets plus total logical storage capacity. Process-local limits
are an additional bound, not the durable quota. Identity/reservation state stays
in a server WeakMap keyed by the request.

After release-aware validation, `ingest_xrbench_report` inserts the report, all
result rows, ownership and accounting, then consumes its reservation in one
transaction. Same-owner identical retries return the original receipt. Content
conflicts, wrong owners and expired/replayed reservations cannot overwrite data.

The SQL package enables RLS and denies direct xrBench table access to API roles.
Only the backend service role can execute the canonical RPCs. Legacy overloads
are closed; unrelated portfolio objects and grants remain unchanged. See the
[database contract](hosted-database-contract.md) for signatures and accounting.

## Resource and credential boundaries

| Boundary | Hosted setting |
| --- | --- |
| Active operations per function instance | 1 |
| Raw report size | 8 MiB |
| Auth / quota calls | 4 seconds each |
| Admission / upload / storage deadlines | 10 / 30 / 20 seconds |
| Pending reservation | 5 minutes |
| JSON depth / values | 32 / 750,000 |
| String length / array entries / object members | 65,536 / 25,000 / 256 |

The generic receiver/parser retain a 32 MiB contract. The hosted adapter fixes a
smaller bound for edge resource limits; complete 221-operator/100-sample reports
fit. Upstream JSON responses are bounded, redirects refused and errors redacted.
A storage success requires the database's exact matching receipt.

Keep `XRBENCH_UPLOAD_PROXY_SECRET` only in Worker and edge secret storage. The
Worker also needs `XRBENCH_SUPABASE_ANON_KEY`; the edge uses Supabase's injected
project URL and service-role key plus its exact `XRBENCH_ALLOWED_ORIGINS` list.
Browser code receives none of these server capabilities. Same-origin fetches
include the private cookie so receipt-loss retries retain the report owner.
Cookie resets can evade a per-browser budget, so global durable attempt, byte and
storage limits are enforced regardless of identity resets. The generic receiver
continues to fail closed without admission.

Reports remain self-reported measurements. Authentication, schemas and hashes do
not attest hardware. Keep public/synthetic uploads separate from independently
qualified hardware evidence. Details: [adapter contract](hosted-adapter-contract.md).
