# xrBench deployment and automatic-upload activation

The current visitor flow is **run the benchmark → automatic completed-result
upload → optional JSON download**. There is no visitor email, benchmark sign-in
or account-creation step. Native evidence stays in the browser while delivery
retries, and JSON downloads remain usable during a receiver outage.

## Current relay: active and verified

The automatic-upload update was published on **October 27, 2026**
(America/New_York), with owner-only access retained. The current source is commit
`d9f705bcced9e8d8a28e794be2ee03f93993bd0d`, deployment
`appgdep_6ae0f9bd44408191a4f02946a7c38f5d`, saved version
`appgprj_6ae0ddb34b808191af7c1f31adeb4ee1~appgver_7ee6167e394481919ab0781e40bb4ee9`,
and server environment revision **1**. The
[deployment record](../artifacts/hosting/automatic-upload-deployment.json)
identifies the published Site and configured edge function. No benchmark emails
or accounts were created. Hosting still has its existing owner authentication
boundary; the benchmark adds no sign-in step.

The [combined readiness record](../artifacts/hosting/automatic-upload-readiness.json)
is `ready: true` and links the final deployment, website, HTTP, SQL, audit and
frontend-VM evidence. Each record's scope is described below.

The live automatic-delivery package targets the standalone Site
`https://xrbench.mickirusu.chatgpt.site/` and its same-origin `/api/reports` route.
The browser contacts neither Auth nor Supabase directly. The Worker signs a
Secure/HttpOnly/SameSite=Strict `__Host-xrbench-upload` cookie to derive a stable
anonymous-browser quota/ownership UUID. It establishes that cookie before a
report can commit and strips incoming proxy/identity headers.

The Worker has two configured server environment values: the private
`XRBENCH_UPLOAD_PROXY_SECRET` capability and the project's legacy anon JWT in
`XRBENCH_SUPABASE_ANON_KEY`. The edge function needs the identical capability in
its secret store and
`XRBENCH_ALLOWED_ORIGINS=["https://michael-notebook-portfolio.mickirusu.chatgpt.site","https://xrbench.mickirusu.chatgpt.site"]` (preserving the existing portfolio origin). These settings and the matching
`submit-report` package are deployed. Supabase's
injected project URL and service-role key remain backend-only. Configure private
values through protected secret storage; do not put them in browser assets,
source, URLs, logs or command arguments.

Gateway JWT checking stays **enabled**. The relay's anon JWT satisfies that
gateway boundary, but only its additional private capability authorizes the
asserted UUID at the handler. The compatible verified-user API path remains
protected by the project's Auth server. No shared Auth settings or direct
anonymous/table grants need to change. Both paths must reserve durable quotas
before body processing, validate the matched runner report, and receive a
transactional PostgreSQL receipt before acknowledging success.

Cookie resets can change the anonymous identity, so per-browser limits do not
replace the global daily attempt/byte and total storage budgets. Reports remain
self-reported evidence. The original native JSON is retained even after an
acknowledgment; a quota or storage failure must not rerun the benchmark.

The existing runner manifest and signed APKs are reused without a native build.
Prepare the matching edge package and standalone Worker from the local checkout:

```sh
node scripts/build-receiver.mjs artifacts/hosting/xrbench-site/public/runner/manifest.json
cd artifacts/hosting/xrbench-site
npm run check
npm run build
```

`check` includes both hosting and upload tests. The regenerated edge bundle
matches the standalone runner APK
`7d18fc075560f0b2584966fda263f4796ad80f5a45865e7c56ae480e8a8f0fc7`.
Deno type-checking and all 16 adapter tests passed locally with the private relay
path enabled. The current verification also includes **193 root JavaScript
tests** and **11 standalone hosting/upload tests**. These controlled tests are
separate from the real HTTP and database evidence below.

### Current live evidence and its limits

| Check | Recorded result | Evidence |
| --- | --- | --- |
| Deployment and protected configuration | Current Worker and `submit-report` deployed; owner-only audience retained, environment revision 1, gateway JWT checking enabled, matching private relay capability and both exact origins configured. | [Deployment record](../artifacts/hosting/automatic-upload-deployment.json) |
| Website HTTP and request boundary | Root/index GET and HEAD, required headers, 25 matching non-HTML assets, signed-cookie attributes, no sign-in UI, and malformed request rejection passed. Cloudflare's inspected HTML bootstrap is handled separately from application bytes. | [Website checks](../artifacts/hosting/standalone-live-checks.json) |
| Real relay → edge → PostgreSQL submission | Two explicit HTTP POSTs of the same marked deployment fixture returned 200 and identical receipt fields. No browser outbox, email or benchmark Auth account was used. | [Live upload checks](../artifacts/hosting/standalone-upload-live-checks.json) |
| Actual committed data | Exactly one run and 221 result rows; payload/report hashes match. All 221 results are unsupported, with zero samples and zero usable timings. This is not device or performance evidence. | [Live SQL verification](../artifacts/hosting/automatic-upload-live-database.json) |
| Published frontend behavior | Production-fetched frontend executed locally with mocked USB, IndexedDB and fetch. Normal delivery confirms a mocked receipt; a mocked 503 retains the exact outbox body and JSON download without rerunning the benchmark. Zero production network requests occur in this VM test. | [Frontend VM checks](../artifacts/hosting/live-frontend-vm-checks.json) |
| Post-activation database security and capacity | All 17 read-only checks pass: deployed RPC definitions/grants, RLS, schema versions, quota ledgers/capacity and unchanged unrelated public objects. | [Final read-only audit](../artifacts/hosting/automatic-upload-post-audit.json) |

The live fixture is `run-3d8d57163a7c475b88a833c2376d3938`. It is explicitly
marked as deployment validation. Its raw report and the saved SQL query are
retained beside the result files; no physical headset ran it. Both successful
POSTs returned payload hash
`85456b7874404fe3ab60541734fd531585efbc8660ccefd221d66deb7b2409a3` and report hash
`1d59072ea6f35fa85f4fd5bd014df22648cdb489b506321dd0bc2e0c661c7889`, with one
stored run after the retry.

The first attempt failed before upstream admission because Cloudflare rejects
the outbound fetch option `redirect: 'error'`, despite Node accepting it. The
deployed compatibility fix uses manual redirects and explicitly rejects all
3xx responses; it does not relax redirect handling. Diagnostics log safe stage
enums, not raw credential-bearing upstream exceptions. The
[pre-retry audit](../artifacts/hosting/automatic-upload-pre-retry-audit.json)
confirmed no stored records or reserved bytes and the full 500-attempt daily
budget before the two successful requests.

The final audit records **498** global daily attempts remaining, **930,132 bytes**
of measured and accounted logical report storage, **zero** reserved bytes, and
**267,505,324 bytes** remaining under the existing 256 MiB application limit.
The shared database measured **11,785,907 bytes**. This read-only audit verifies
the application ledger and existing quota capacity; it does not certify provider
disk headroom, WAL, backups or other portfolio usage. Unrelated public
objects/grants match their baseline hash. No shared Auth settings, email delivery
or benchmark-account setup changed.

Actual browser/headset execution and physical device qualification remain separate
from this activation. The VM test's fetch and USB are mocked; the live HTTP/SQL
smoke has no hardware samples. A real receiver outage was not induced in
production. Keep these boundaries when reporting readiness or performance.

## Earlier verified deployment: historical download-only flow

The canonical address is now [xrbench.mickirusu.chatgpt.site](https://xrbench.mickirusu.chatgpt.site/),
published as a separate owner-only Site on October 27, 2026. See
[standalone hosting](standalone-hosting.md) for its identity and current checks.
The portfolio keeps its existing address and links to this standalone Site.
The older `/xrbench/` section remains available for results recorded at that origin.

The section deployment recorded below was prepared in the companion MichaelsPortfolio project.
The separate backend targets Supabase project `nqsieptekarxxmqrgmdy`; its dashboard
currently labels that reference **xrML**. The existing portfolio audience remains
unchanged.

That section's deployment package was `artifacts/hosting/michaelportfolio/`. It was
installed into the companion portfolio's `public/xrbench/` directory using the
validated, managed installer. Its CSP permits connections only to its own origin.
It contains no Auth key, access token, email form or upload endpoint setting.

The website and Supabase backend were deployed on **October 26, 2026**
(America/New_York). The live section is
[the portfolio's xrBench page](https://michael-notebook-portfolio.mickirusu.chatgpt.site/xrbench/).
The owner-only hosting audience was explicitly retained. Hosting access still
requires the owner's ChatGPT session; the benchmark itself has no sign-in flow.

Both reviewed database migrations are installed, and the `submit-report` Edge
Function is deployed with gateway JWT checking enabled. A read-only audit passed
**all 17 checks**, including exact RPC hashes, seven RLS-enabled tables, denied
client/table access, quota ledgers and unchanged unrelated public objects. A live
service-role reservation smoke test passed and was rolled back. A second audit
confirmed zero stored/reserved report bytes and the full 500-attempt daily budget.
The observed physical database size afterward was **11,122,355 bytes**.

The published source is commit `9b7edb45577c22405229ef273e9b68d80cfeaff9`,
version `appgprj_6a5995d8d8048191a19799b0a800ab81~appgver_344f440161948191baaa195191daa3f6`,
deployment `appgdep_6ae0024c4fc8819195fe1f7efa49ae44`.
It was built from an isolated remote-source checkout to exclude unrelated local
portfolio edits. The reviewed hosting/security files were also synced back to the
companion source without staging, reverting or publishing its other local work.

| Gate | Evidence / state |
| --- | --- |
| Public visitor experience | Download-only browser mode preserves native evidence and existing queued data without contacting Auth or uploading reports. |
| Website build/package | Matched release, signed APK hash, public asset allowlist, scoped headers and intended project/origin/path are checked. |
| Portfolio integration | Standalone route, ordinary navigation, Cloudflare worker-first assets and Next rewrite/header integration are implemented. |
| Durable database quotas/RPC | Isolated PostgreSQL tests cover concurrency, ownership, rollback, retries, expiry, replay, grants and unchanged portfolio objects. |
| Production edge code | Generated bundle checks/runs under Deno 2.9.6; the PostgreSQL-backed test verifies a full 221-operator/22,100-sample submission and quota denial. |
| Hosted database activation | Deployed; all 17 live read-only audit checks passed, plus the rolled-back service-role quota smoke. |
| Protected edge activation | Deployed with JWT checking enabled and the exact portfolio origin; live unsigned requests return 401 and untrusted origins return 403. |
| Website publication | Published successfully with owner-only access retained; live routes, headers, assets, navigation and request guard passed. |

The selected portfolio Site currently lives at
`https://michael-notebook-portfolio.mickirusu.chatgpt.site`, with no Sites custom
domain attached. The source's `https://www.michaelml.dev` SEO setting alone does
not prove it is the deployed domain. Keep build origin and actual destination
matched. Changing the existing Site's audience is a separate action.

## Reproduce the historical portfolio section

These steps reproduce the older download-only portfolio section, rather than
activate the canonical standalone relay. Current `.env.hosting.example` and the
signed-release workflow default to automatic same-origin delivery at `/`.
The explicit overrides below retain the historical section mode and origin.

```sh
XRBENCH_REPORT_DELIVERY=download XRBENCH_REPORT_ENDPOINT= XRBENCH_SITE_ORIGIN=https://michael-notebook-portfolio.mickirusu.chatgpt.site XRBENCH_SITE_PATH=/xrbench/ XRBENCH_WEB_OUTPUT=artifacts/hosting/michaelportfolio node --env-file=.env.hosting scripts/build-web.mjs
node --experimental-vm-modules scripts/verify-web.mjs artifacts/hosting/michaelportfolio
node scripts/verify-hosting.mjs artifacts/hosting/michaelportfolio --project-ref nqsieptekarxxmqrgmdy --path /xrbench/
node scripts/build-receiver.mjs artifacts/hosting/michaelportfolio/runner/manifest.json
node scripts/install-portfolio-section.mjs --dist artifacts/hosting/michaelportfolio --portfolio ../MichaelsPortfolio --check
node scripts/install-portfolio-section.mjs --dist artifacts/hosting/michaelportfolio --portfolio ../MichaelsPortfolio
```

The installer rejects the wrong project, an obsolete sign-in/upload package,
unmanaged directories, symlinks and modified managed assets. Its marker stays
outside `public/`. Rebuild the companion portfolio after installation. Its
`docs/xrbench-hosting.md` describes the scoped route/header integration.

In that historical mode, the browser verifier executes the actual bundle, opens a stubbed USB chooser,
recovers a complete native report, clicks Download results JSON and checks the
exported native bytes. In that download-only mode there must be no Auth or report POST
requests and no new outbox submissions. Existing local automatic PostgreSQL
workflows remain available through the private developer/test services.

## Reproduce or audit the separate protected backend

Review [the database contract](hosted-database-contract.md). Put the intended
project's administrative connection in `XRBENCH_MIGRATION_DATABASE_URL` through
protected secret handling. Remote connections require verified TLS and trusted
CA configuration. Runtime credentials are never used for hosted schema setup.

```sh
uv run --locked python -m engine.hosted_deployment --check
uv run --locked python -m engine.hosted_deployment --apply
uv run --locked python -m engine.hosted_deployment --check
```

`--check` is read-only. `--apply` explicitly applies only the two reviewed SQL
files atomically, then audits. The files create `xrgpu_benchmarks`,
`xrbench_private` and two named public RPCs. They preserve unrelated portfolio
objects/grants and close legacy overloads. Anonymous and authenticated client
roles cannot read xrBench tables or call these RPCs. The backend service role
can execute the canonical RPCs and has no direct table grants.

The audit verifies deployed function definitions, privileges, RLS, schema versions,
quotas, ledger consistency and capacity for a 33 MiB reservation. Physical database
size is a metric; check provider capacity, backups, WAL and other project usage
before raising storage limits. Defaults are 20 attempts/256 MiB reserved input
per principal per UTC day, 500 attempts/4 GiB globally, 256 MiB committed plus
reserved logical report storage, and five-minute pending reservations.

The current edge code accepts the trusted relay's capability and server-derived
UUID, or a verified Supabase user token from a private caller. It reserves quota
before reading a report and sends success only after a transactional receipt.
Its 8 MiB/one-active-upload limits and global database budgets apply to either
path. See [the adapter contract](hosted-adapter-contract.md).

Supabase CLI 2.122.0 was checked. Authenticate it through the normal account flow,
then deploy only the named function; no prune or JWT bypass is needed:

```sh
npm exec --yes --package=supabase@2.122.0 -- supabase secrets set --project-ref nqsieptekarxxmqrgmdy 'XRBENCH_ALLOWED_ORIGINS=["https://michael-notebook-portfolio.mickirusu.chatgpt.site","https://xrbench.mickirusu.chatgpt.site"]'
npm exec --yes --package=supabase@2.122.0 -- supabase functions deploy submit-report --project-ref nqsieptekarxxmqrgmdy --use-api
```

The function uses Supabase's injected backend project URL and service-role secret.
Gateway JWT checking stays enabled. The adapter independently validates the
private relay capability or verifies private-client tokens with the project's
Auth server. Configure the matching private capability separately through secret
storage when rotating or preparing a replacement deployment. Both current secret
stores are configured; update them together during rotation. Keep backend tokens out of browser configuration.
A matching receiver release manifest must accompany each signed website runner.

## Historical live website verification

The owner-private live checks used Sites' existing service access through a
hidden stdin input; its token was never written into source, artifacts, URLs or
browser settings. The simple `verify-hosting.mjs --live` command needs an
authorized fetcher for this private Site. The recorded private verifier is in
`artifacts/hosting/verify-live-deployment.mjs` and fails if policy, assets or
request boundaries differ from the reviewed deployment.

All three document forms (`/xrbench`, `/xrbench/`, `/xrbench/index.html`) pass GET
and HEAD with HTTP 200 and the required CSP, USB, referrer, framing and HTTPS
headers. Sites serves physical assets before Worker routing settings, so the
build embeds the canonical HTML in the Worker and omits only its compiled static
`index.html` and `_headers`. All 24 supporting assets remain byte-identical.
Hidden paths and the unpublished header file return 404. The homepage contains
the ordinary xrBench navigation link. The Worker rejects unsupported React
Server Action headers and page/form POST requests before framework dispatch.

Cloudflare inserts a challenge bootstrap into HTML, as described in its
[JavaScript Detections documentation](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/javascript-detections/).
The application HTML matches exactly after excluding only the inspected bootstrap
template (normalized SHA-256 `6d6461bd4913a7ca818f91936d4eac138eba3a8fd68f90a74203f65c7f4b29cf`).
Any other HTML difference or changed bootstrap fails the check. The same-origin
script policy remains enforced; it was not relaxed for inline scripts.

The browser harness then executed the **actual production-fetched bundle** and
completed Run, restored a full native report, clicked the JSON download control,
and checked the exported evidence bytes. It made no Auth or report upload calls.
The hosted site and tests created no visitor accounts or sent email.

The final evidence is in `artifacts/hosting/live-website-checks.json`,
`live-website-entry-checks.json`, `live-database-audit.json` and
`live-backend-smoke.json`. No physical headset run occurred during deployment;
USB cancellation and result download were verified with controlled fixtures.
Physical provider storage headroom is not certified by SQL's database-size metric.
Existing portfolio dependency advisories and the deployed request-boundary
mitigation are recorded in its `docs/hosting-security-exceptions.md`.

## Recorded verification before automatic-relay activation

| Check | Result |
| --- | --- |
| Full JavaScript browser/server suite | **181 passed** after removing the email flow. |
| Full Python suite | **568 passed**, 47 optional checks skipped. |
| Hosted PostgreSQL migrations/RPC | **14 passed** in isolated temporary databases. |
| Deployment/audit helper | **11 passed**, including read-only checks, bad-grant denial and atomic rollback. |
| Actual Deno bundle → PostgreSQL | **1 passed** with a controlled Auth/PostgREST bridge and complete 221×100 report. |
| Final portfolio hosting/security checks | **15 passed** in both the isolated deployment checkout and the local portfolio; installer **2 passed** during preparation. |
| Packaged browser execution | Exact native JSON download passed; no sign-in, Auth or report POST requests. |
| Final portfolio build and local HTTP preview | Passed; both root forms/assets return 200 with scoped headers; hidden/unpublished files return 404. |
| Chrome local visual check | Download-only copy shown; no email or sign-in controls. Live verification used authorized HTTP and the production-bundle harness; the owner browser requires MFA. |
| Edge bundle and source checks | Deno 2.9.6 type-check, scoped Ruff, workflow YAML and both repositories' diff checks passed. |
| Live deployment | Website policy/asset/navigation/guard checks, 17 database checks, quota rollback and Edge 401/403 checks passed. |
| Production-fetched browser bundle | Run and exact JSON download passed with no email, sign-in or upload requests. |

The hosted-security workflow runs isolated database/Deno tests without production
credentials. The records above used download-only packaging. The current signed-
release workflow prepares automatic same-origin delivery and uploads the matched
protected backend package separately. The current relay is activated and verified
by the evidence at the start of this document; repeat those checks for subsequent
updates instead of reusing the historical download-only results.
