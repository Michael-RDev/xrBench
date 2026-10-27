# Security review for internet hosting

Review date: 2026-10-26. Three read-only review agents examined the public browser
and receiver, Python HTTP services, and PostgreSQL configuration. Three separate
remediation agents were assigned the confirmed findings. The coordinating agent
also audited locked dependencies and checked tracked files for common private-key,
GitHub-token, and AWS-access-key patterns. Existing working-tree changes were
preserved.

The hosting follow-up now supplies an activated same-origin automatic-upload
relay, JSON downloads, durable PostgreSQL quotas and reservation-bound RPCs.
See [deployment readiness](deployment-readiness.md) for the current Site/edge
deployment, real HTTP receipts, committed SQL rows and all 17 post-activation
audit checks. The benchmark has no email or sign-in flow; the Site retains its
owner-only hosting audience. The findings below retain the original exploit
evidence and remediation context.

The original October 26 review covered source and local reproductions; that stage
deployed no website and changed no existing database permissions or data. The
October 27 activation is recorded separately in the handoff. Its marked live
fixture has no hardware samples, and neither stage certifies physical-device
qualification, backups or provider disk headroom.

## Findings and evidence

Priority reflects impact in the described deployment, rather than a claim that the
default loopback-only development setup is remotely exposed.

| Priority | Finding | Reproduction / prerequisites | Remediation |
| --- | --- | --- | --- |
| P1 | Hosted report handler treated allowed `Origin` as its only admission gate | An HTTP client supplied an allowed Origin and fabricated three complete 221-operator reports of about 2.5 MB each using public manifest fields. All received HTTP 200 from an in-memory store; no physical device was needed. Directly exposing this handler could permit database spam and false measurements. | Require trusted admission before reading uploads; fail closed when it is absent. Bound resource use and require deployment-level durable quotas. See [receiver hosting](report-receiver-hosting.md). |
| P2 | Receiver uploads lacked read deadlines, concurrency limits, and JSON complexity budgets | A partial stream remained pending until closed. A report with 2 MB of unrelated padding passed validation. Byte limits alone did not bound recursive processing or retained metadata. | Bound upload time, concurrent work, and JSON structure; apply limits to direct parser calls as well as HTTP. |
| P2 | Python static handlers served hidden files and symlink targets | Both handlers returned HTTP 200 for a fake `/.env` and for a symlink pointing to a fake file outside the site root. A sensitive file or link must first land in the served tree. | Constrain public file paths, hidden segments, symlinks, and resolved containment. See [local server security](local-server-security.md). |
| P2 | Python HTTP services allowed unbounded stalled request threads | Eight incomplete-header connections created eight worker threads with no header timeout. Exploitation requires reaching the listener, such as through a public proxy or tunnel. | Bound active requests and apply deadlines before parsing headers and bodies; retain loopback bindings. |
| P2 | Remote storage connections did not require verified encryption | An external PostgreSQL DSN was passed to libpq without enforcing secure settings. The optional Supabase adapter also accepted a remote HTTP endpoint, which could expose its service key. Both require a deployment configuration enabling the weak transport and a network attacker. | Require `sslmode=verify-full` for remote or ambiguous PostgreSQL connections and HTTPS for remote Supabase endpoints; preserve explicit local development connections. See [database hosting](database-hosting.md). |
| P2 | External runtime startup required migration privileges | Startup ran migrations through the same connection used for report reads/writes. Reusing the local Docker superuser in production would further amplify backend credential compromise. | External startup checks readiness only; use separate administrative migrations and a restricted runtime role. Existing local volumes remain compatible. |
| Conditional hosting blocker | Developer session tokens provide local browser protection, not public user authorization | A proxy that forwards requests as localhost can let its clients obtain `/api/session`, then control attached devices and read reports. Default loopback/Host checks prevent ordinary direct internet access. | Keep developer/device-control services private. Reject detectable forwarded requests as defense in depth. Publish packaged assets and a separately protected ingestion endpoint. |

Report admission authenticates an uploader or gateway; it does not attest hardware
or prove that measurements were performed. Accepted benchmark reports remain
self-reported evidence. An authenticated malicious uploader can still fabricate
results, so public rankings must not present them as independently verified.

The SQL paths reviewed use bound parameters for report values and pagination, and
report writes are transactional with digest checks on conflicting run IDs. Review
agents found no confirmed SQL injection, browser XSS, shell injection, or bundled
credential leak in the inspected paths. The limited tracked-file secret-pattern
scan found no matches; it was not a Git-history or comprehensive secret scan.

## Dependency audit

`npm audit --json` reported zero known vulnerabilities in the locked npm tree.
An isolated `pip-audit` run against the locked Python export, including development
and optional export dependencies, initially reported 11 advisory entries across
four packages. Certifi and pytest each appeared twice under the same advisory;
there were nine distinct advisory identities after deduplication.

| Package before remediation | Patched minimum | Locked and installed after remediation | Context and primary source |
| --- | --- | --- | --- |
| certifi 2026.7.22 | 2026.10.19 | 2026.10.23 | Compromised CA trust affecting Python HTTPS clients; [maintainer advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2). |
| filelock 4.1.1 | 4.2.1 | 4.2.4 | Six local lock-related advisories, generally requiring access to lock directories or platform-specific conditions; [maintainer advisory example](https://github.com/tox-dev/filelock/security/advisories/GHSA-2g95-wg33-c5x2). |
| pytest 8.4.2 | 9.0.3 | 9.1.1 | Development/test temporary-directory handling; [maintainer release](https://github.com/pytest-dev/pytest/releases/tag/9.0.3). |
| setuptools 84.0.0 | 84.1.0 | 84.2.1 | Windows legacy wheel installation path escape; [maintainer advisory](https://github.com/pypa/setuptools/security/advisories/GHSA-xmj3-9gf7-h52w). |

These findings do not establish four remotely exploitable website bugs. The
optional native export/build environment has a larger dependency set than the
hosted static browser or a minimal report receiver. Avoid installing development
and export tooling into a production receiver image.

Only these four package versions changed in the lockfile. The existing project
virtual environment was synchronized with `uv sync --locked --all-extras --group
dev`. A fresh audit of the export found zero known advisories in the 62 packages
selected for this macOS/Python environment. Platform markers limit this result;
repeat the audit in the actual production platform/image.

## Operational deployment boundaries

- Serve the packaged website over HTTPS with the generated security headers.
- Keep local Python developer/device-control services and PostgreSQL listeners
  private. Forwarding headers are detectable hints, not an authentication boundary.
- Keep the activated relay admission and durable PostgreSQL quotas enforced,
  and repeat the [deployment checks](deployment-readiness.md) after updates. A public
  JavaScript API key or Origin allowlist is insufficient.
- Apply migrations with administrative credentials, then use a restricted runtime
  database role and a verified TLS connection. Verify actual cloud grants and
  network access before deployment.
- The Supabase RPC/schema and edge entrypoint are deployed and audited. The
  website invokes ingestion only through its protected same-origin server relay;
  browser code receives no server capability or direct database access.

## Original review verification

These counts describe the original remediation run. Current activation and
verification results are linked in the [deployment handoff](deployment-readiness.md).

| Check | Result |
| --- | --- |
| `npm test` | 155 passed, zero failures. |
| `.venv/bin/python -m pytest -q` | 534 passed, 23 optional checks skipped, zero failures. |
| Focused HTTP/report/native regression suite | 98 passed, 2 irrelevant developer-file cases skipped. Includes all 221 operators and 22,100 samples. |
| Local PostgreSQL integration (`XRGPU_TEST_POSTGRES=1`, restart disabled) | 9 passed, 1 container-restart check skipped. Only UUID-named temporary databases were created/dropped. |
| Restricted-role integration (`XRGPU_TEST_POSTGRES=1 .venv/bin/python -m pytest engine/tests/test_database_role.py -q`) | 1 passed. A real temporary runtime login passed schema health, insertion, retry and reads; all 11 history mutations/DDL attempts were denied. Its temporary database and role were removed. |
| `npm run build:receiver` | The original receiver library bundled successfully; the subsequent hosting follow-up supplied and deployed its adapter/RPC. |
| npm and Python dependency advisory audits | Zero known advisories after the Python lock updates. |
| New Python helper/test lint and `git diff --check` | Passed. Existing compact-style E701/E702 findings in `engine/server.py` and `engine/tests/test_postgres.py` remain; no new findings were introduced. |

Regressions cover spoofed Origin with missing/denied admission, quota denial before
reading a body, upload interruption, slow-drip and storage deadlines, concurrent
capacity recovery, byte/depth/node/string/collection budgets, unencrypted storage
endpoints, hidden/symlink/encoded file paths under GET and HEAD, ambiguous framing,
proxy headers, PostgreSQL TLS overrides, and external startup without DDL. Existing
complete native reports and deterministic idempotent receipts remain supported.

The follow-up live HTTP and read-only database audits verify the deployed relay,
receipts, RPC definitions, privileges, quota ledgers and unchanged unrelated public
objects. Physical hardware execution, provider disk headroom and backup restoration
remain outside those checks. No synthetic protocol test establishes device
qualification.
