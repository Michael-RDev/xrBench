# Supabase edge receiver contract

The deployable entrypoint is `supabase/functions/submit-report/index.ts`. Run
`npm run build:receiver` after packaging the matched website/runner to generate
its self-contained `receiver.mjs` bundle and `releases.json`. No SDK or remote
module is downloaded when the function starts. Invalid environment configuration
or release manifests stop startup.

## Configuration and authentication

The function reads `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and
`XRBENCH_ALLOWED_ORIGINS`, and optional `XRBENCH_UPLOAD_PROXY_SECRET`. The origin
setting is a JSON array of exact website origins,
for example `["https://bench.example"]`. Remote URLs must use HTTPS and cannot
contain credentials, paths, queries, or fragments. Explicit localhost HTTP is
accepted for isolated development. Keep the service-role key in backend secrets;
the browser does not need this secret or an Auth key.

`supabase/config.toml` keeps `verify_jwt = true` as a gateway check. Automatic
website uploads use a same-origin server proxy. That proxy sends the project's
legacy anon JWT for the gateway check and authenticates to this handler with
`x-xrbench-proxy-key` and a server-derived `x-xrbench-principal` UUID. The private
proxy capability is a canonical, unpadded base64url encoding of 32 random bytes.
It belongs only in server secret storage at the proxy and edge function, never in
browser JavaScript, HTML, report JSON, logs or public deployment metadata.

The handler verifies the capability through WebCrypto HMAC verification using
its fixed-length, provider-side MAC comparison. Any proxy assertion header
without both a valid private capability and a valid UUID is rejected; it cannot
fall back to user authentication. Proxy headers are not allowed by browser CORS.
The server proxy must strip incoming proxy/identity headers and derive the UUID
from its own anonymous-browser identity mechanism, rather than trust a browser
header or request field. A signed HttpOnly identifier can make retries stable
without collecting an email or creating an Auth account.

When proxy assertion headers are absent, the compatible private-client path still
requires `Authorization: Bearer <user access token>` and calls the configured
project's `/auth/v1/user` endpoint. Its user must have a valid UUID, the
`authenticated` role, a confirmed email, and must not be anonymous. A public API
key, service-role key, unverified user, forged Origin, client-supplied user ID or
decoded JWT payload does not authorize this path.

Existing verified users of the shared project can supply tokens to an authorized
private API client. The public website automatically uploads completed reports
through its server proxy and retains JSON downloads without contacting Auth,
sending email, creating accounts or changing global Auth settings. Accepted
reports remain self-reported measurements; the proxy's anonymous-browser UUID is
a quota/ownership key, not a verified person or hardware attestation. A visitor
may reset browser identity; the database's global request/storage budgets must
remain enforced regardless of those resets.

Supabase documents [server-side getUser verification](https://supabase.com/docs/reference/javascript/auth-getuser)
and [function JWT checks](https://supabase.com/docs/guides/functions/auth-headers).

## Request and database boundary

After validating a trusted proxy or authenticating a private client, the adapter
calls `reserve_xrbench_upload` with its server-established
`p_principal` UUID and the fixed `p_max_bytes` limit of 8,388,608 (8 MiB). It validates
the returned `reservation_id`, principal, maximum size, and expiration before
reading the report body. The private database policy decides the principal/global
request and byte budgets and the total storage reservation; the browser cannot
choose these values.

The verified identity and reservation live only in a server WeakMap keyed by the
request. After validating the supported runner report, the adapter calls
`ingest_xrbench_report` with the four report/projection/hash arguments plus
`p_principal` and `p_reservation_id`. A successful response requires a database
receipt matching the report run ID and both hashes. The database RPC must restrict
execution to the server role, consume reservations transactionally, reject
ownership/content conflicts, and return deterministic retry receipts.

| Condition | HTTP result |
| --- | --- |
| Missing/invalid/disabled proxy capability or malformed asserted UUID | 401; no fallback authentication |
| Missing/malformed/expired/wrong-project user access token | 401; sign in again |
| Anonymous/unverified/invalid authenticated account | 403; verified email required |
| DB budget exceeded (`P0001`) | 429 with `Retry-After` until the next UTC daily window |
| Run ID ownership/content conflict (`23505`) | 409 |
| Expired/invalid reservation (`42501`), missing RPC, malformed receipt, Auth/DB outage | 503; safe retry |
| Upload timeout or report resource limit | 408 or 413 |

`Retry-After` is exposed through CORS. A full total-storage budget may still need
operator retention/capacity work after the next daily window; the retry header is
a client backoff bound, not a promise that capacity will become available.

The hosted adapter accepts at most one active request and an 8 MiB report per
function instance to bound payload/projection memory. These fixed hosted limits
cannot be relaxed by generic receiver options. The generic reusable receiver and
direct parser retain their 32 MiB limit. The complete 221-operator/100-sample
protocol fixture fits the hosted limit.

All upstream calls refuse redirects, omit browser cookies, and have total I/O and
JSON response size limits. Default Auth and quota calls each have four-second
bounds, admission has a ten-second bound for those sequential calls, storage has
a 20-second bound, and the generic receiver adds upload, storage and concurrency
bounds. Authentication and database error bodies
are never exposed to visitors, and tokens/service credentials are not logged.

## Verification limits

The adapter tests exercise valid/bad/partial/disabled proxy capabilities, constant-
length mismatches, UUID/origin restrictions, verified-user compatibility, getUser
failures, invalid users, authentication-before-
quota-before-body ordering, request-bound reservations, quota/timeout failures,
receipt/conflict checks, complete 221-operator reports, and idempotent retries.
They use controlled upstream responses. Real gateway JWT handling, configured
project token verification, deployed RPC grants, production origins and signed
runner manifests must also pass the deployment readiness checks before publishing.
