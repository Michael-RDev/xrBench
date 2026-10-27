# Standalone xrBench hosting

The canonical address is **https://xrbench.mickirusu.chatgpt.site/**.
Sites includes the account namespace in its generated address.

The standalone Site is `appgprj_6ae0ddb34b808191af7c1f31adeb4ee1`.
Its source checkout is `artifacts/hosting/xrbench-site/`, with its own
`.openai/hosting.json` and source repository. Reuse this identity for updates;
do not create another Site. The initial published source is commit
`ff30d15e1e1c75226fb0724903e03406fbe03e53`.

Automatic uploads are **active and verified as of October 27, 2026**. Completed
reports go to the same-origin `/api/reports` relay; the original native JSON,
browser evidence and JSON downloads remain available. The Site retains
owner-only access. That hosting boundary still requires authorized owner access;
the benchmark adds no email, account creation or sign-in step.

The current published source is commit
`d9f705bcced9e8d8a28e794be2ee03f93993bd0d`, deployment
`appgdep_6ae0f9bd44408191a4f02946a7c38f5d`, saved version
`appgprj_6ae0ddb34b808191af7c1f31adeb4ee1~appgver_7ee6167e394481919ab0781e40bb4ee9`.
Its server environment is revision **1**. The
[activation record](../artifacts/hosting/automatic-upload-deployment.json)
records the deployed Site/edge configuration without private credential values.

The Worker authenticates to Supabase project `nqsieptekarxxmqrgmdy` with a private
server-to-server capability. Its configured server environment contains
`XRBENCH_UPLOAD_PROXY_SECRET` and `XRBENCH_SUPABASE_ANON_KEY` (the project's legacy
anon JWT for the still-enabled gateway JWT check). The matching edge secret and
exact standalone and retained portfolio origins are configured on `submit-report`,
which is deployed with gateway JWT verification enabled. The anon JWT alone
does not authorize an upload, and no service-role key or private capability is
included in browser assets. The public page uses `connect-src 'self'` and never
contacts Auth or sends email.

A signed `__Host-xrbench-upload` cookie supplies the anonymous browser's stable
quota/ownership UUID. It is Secure, HttpOnly, SameSite=Strict and scoped to `/`.
The document prepares it before uploads; a request without a valid cookie gets
428 and a cookie before any report can commit, then retries automatically.
Same-origin delivery includes the cookie. The relay discards client identity
headers and supplies its own UUID after signature validation. Deleting cookies
can reset that identity, so PostgreSQL's global budgets remain mandatory alongside
per-principal quotas. These UUIDs are not verified people or hardware identity.

The existing signed runner distribution is retained; the automatic-upload update
rebuilds the website assets without rebuilding native code. The source checkout
uses a small dependency-free Worker to serve the root HTML with CSP and USB
policy. Its build omits the static entry and header files so Sites cannot serve
the document before the Worker. The signed APKs and matched runner manifest
remain unchanged by this update.

The portfolio Site `appgprj_6a5995d8d8048191a19799b0a800ab81` retains its own
address. Its xrBench navigation points to the standalone Site. Its original
`/xrbench/` section is retained because browser results belong to the origin
where they were recorded. Moving to another origin does not automatically move
those stored results; they remain downloadable through the original section.

For the standalone source checkout, run:

```sh
npm run check
npm run build
```

These use Node built-ins and require no dependency installation. The root
repository's `.env.hosting.example` and release workflow now default to this
standalone origin and `/`. To prepare a new signed distribution for this Site,
validate it with `scripts/verify-hosting.mjs --origin https://xrbench.mickirusu.chatgpt.site --path /`
before replacing the standalone checkout's `public/` tree and rebuilding.

The Site's `check` command runs both hosting and upload tests. They cover
root/index GET and HEAD, headers, approved assets, path rejection, tampered
cookies, upstream identity/header isolation, receipt handling, byte limits,
deadlines and quota backoff. The browser harness verifies automatic delivery and
exact JSON download with no Auth requests; outage coverage retains the local
report. Current verification separates these test boundaries:

- [Live website checks](../artifacts/hosting/standalone-live-checks.json) confirm
  automatic-delivery metadata, policy headers, asset hashes, signed-cookie
  attributes and request rejection before upstream admission.
- [Actual HTTP submission checks](../artifacts/hosting/standalone-upload-live-checks.json)
  sent one clearly marked deployment fixture twice through the deployed relay.
  Both POSTs returned 200 with the same committed receipt. This helper used real
  HTTP, independently of the browser outbox.
- [Live PostgreSQL verification](../artifacts/hosting/automatic-upload-live-database.json)
  found exactly one run and 221 result rows with matching hashes. Every result is
  unsupported, with zero samples and zero usable timings; this is deployment
  validation, not a hardware benchmark.
- [Published-frontend VM checks](../artifacts/hosting/live-frontend-vm-checks.json)
  execute production-fetched JavaScript locally with mocked USB, IndexedDB and
  fetch. They verify automatic delivery, exact JSON download and outage retention
  without production network requests. They do not replace the live HTTP and
  PostgreSQL checks above.
- [Post-activation read-only audit](../artifacts/hosting/automatic-upload-post-audit.json)
  passes all 17 checks, with no reserved storage, matching storage ledgers and
  unchanged unrelated public objects/grants.

The first relay attempt exposed a Cloudflare incompatibility with the outbound
`redirect: 'error'` option. It reached no database admission or storage. The
deployed fix uses manual redirects and explicitly refuses 3xx responses, retaining
the redirect boundary; failure logs contain bounded stage enums rather than
credential-bearing upstream errors. The
[pre-retry audit](../artifacts/hosting/automatic-upload-pre-retry-audit.json)
records zero stored/reserved bytes and all 500 daily attempts before the two
successful POSTs. No emails or benchmark accounts were created, and no physical
headset run occurred.

Private verification credentials are supplied through hidden stdin and never
stored with source or artifacts. Historical download-only deployments remain
documented in the [deployment handoff](deployment-readiness.md); they are not
used as proof of the current relay.

See [receiver boundaries](report-receiver-hosting.md) and the
[activation handoff](deployment-readiness.md) for backend settings, evidence and
remaining verification boundaries. Rotating the current shared relay capability also invalidates its signed
browser cookies; plan cookie identity continuity when rotating it.
