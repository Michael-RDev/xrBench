# Quest 3 USB repair: deployed, hardware verification pending

Updated October 27, 2026. The Quest is unavailable; the user explicitly selected
software cancellation repair plus tracing and deployment without live hardware testing.
**The original Quest connection stall has not been verified fixed.** No new native
GPU run or hardware report was produced or inspected.

## Current repair and evidence

- GitHub branch: `quest3-usb-auth`, based on `a75996b` (merged prior handoff).
  Repair commit: `2ed205b6011bccd15d85fdc0235648416bd60b9b`.
  GitHub `main` is not part of the push. The pre-existing deletion of
  `.env.hosting.example` remains unstaged and untouched; the existing branch-name
  correction in this document is retained.
- Inspected the separate hosted repository at the deployed baseline
  `48f9955a6ef24bcc0ab2ad7623d0610126908a7d` before editing.
  Its retained checkout is `/Users/michael/Desktop/xrBench/artifacts/site-quest3-usb`,
  also on `quest3-usb-auth`. Only its configured remote `main` was advanced for
  publication, as explicitly agreed; no replacement Site was created.
- Reproduced a software defect with the real authentication library: the previous
  timeout closed USB but left `connect()` pending if the authentication stream
  did not settle. This is software evidence, not proof of the Quest stall's cause.
  A normal simulated public-key/CNXN exchange completed with the existing library.
- `usb-connection.mjs` now races opening/authentication against cancellation and
  deadlines. The caller settles independently of raw close. Abandoned attempts
  retain ownership until their operation and cleanup settle; late opening or
  authentication is closed before another attempt is allowed. Stuck cleanup
  blocks retry with reconnect/reload guidance instead of another approval request.
- Setup remains 60 seconds. The separate 180-second approval window starts only
  after the public-key packet's actual consumed write completes. CNXN receipt
  changes the message to authentication finalization, never another approval.
  Reconnect timeouts no longer assert that a prior benchmark never started.
- `connect(device, signal, onProgress, onTrace)` adds an optional trace callback.
  The existing session diagnostic download exposes the last 128 entries at
  `validation.connection_trace`, including failed attempts. Events record stage,
  AUTH/CNXN command, AUTH subtype, completion/failure and elapsed milliseconds.
  They contain no RSA material, packet payloads or serials and do not enter native
  report JSON or automatic report uploads. Relevant events are
  `packet_write_started`, `packet_write_complete`, `packet_write_failed`,
  `packet_received`, `authenticate_complete`, `connected`, `failed`, `canceled`,
  `cleanup_complete` and `cleanup_blocked`.
- The hosted build uses a repeatable patcher with the baseline bundle SHA-256 and
  four exact transport/controller regions. It injects the identical tested
  runtime and rejects source drift; unrelated bundle code is preserved. The old
  `scripts/usb-connection-source.txt` is historical, not the active implementation.
- The clean Mac checkout initially passed 11/12 hosted checks: CSS SRI matched
  Windows CRLF bytes while Git stored LF bytes. Rebuilding recalculated integrity
  from actual bytes. All final hosted checks pass; the deployed Chrome page has
  matching JS/CSS integrity attributes and no captured console warnings/errors.

## Current deployment

- Site: <https://xrbench.mickirusu.chatgpt.site>
- Project: `appgprj_6ae0ddb34b808191af7c1f31adeb4ee1`
- Hosted source commit: `463c5a3850b38e882581adeb0c593d4932b89c72`
- Saved version: **10**,
  `appgprj_6ae0ddb34b808191af7c1f31adeb4ee1~appgver_cbd7b0f428c481918399d637a2974877`
- Deployment: `appgdep_6ae14d5b3b0c81919f8d707feb9c7aac`, native result `succeeded`,
  October 27 at 9:18 PM America/New_York (`2026-10-28T01:18:27.924599Z`).
- Owner-only access is unchanged: custom policy revision 1, one owner, zero
  external visitors. Runtime environment revision remains 1; no secrets changed.
- Site metadata SHA-256, confirmed in the real HTTPS Chrome document:
  `e498b40bd60b6ebbba26fb98cfa2b435835985e54363038f9ad6f6d13b609bb4`.
- Live JS integrity:
  `sha256-Bre11bzNPyBEFggSXf6EfNnWQLnJBlVUgU6cseoJy7I=`.
- Runner version 12 and signed package are unchanged. Verified APK SHA-256:
  `7d18fc075560f0b2584966fda263f4796ad80f5a45865e7c56ae480e8a8f0fc7`.
- Retained archive: `artifacts/quest3-usb-auth-site.tar.gz`.
  Ignored software logs and structured evidence: `artifacts/quest3-usb-auth-evidence/`.
  These are local investigation artifacts, not native benchmark results.

## Final software validation

- `npm test`: **208/208 passed**.
- Transport/controller/USB connection tests: **59/59 passed**.
- Hosted `node scripts/build.mjs` then `node --test tests/*.mjs`: **16/16 passed**.
- Tests cover hanging USB close/authentication, cancellation during opening and
  authentication, late results, cleanup ownership, failed prior close, retry,
  stalled/failed packet writes, real WebUSB serialization and consumable
  backpressure, signature/public-key auth, CNXN versus authentication completion,
  trace persistence/bounds/export, unchanged upload payloads and hosted patch drift.
- The hosted suite executes the actual patched transport with its bundled Tango
  library using simulated packet streams. These are explicitly software tests.
- Actual HTTPS Chrome verification: new build identity and SRI match, Run enabled,
  disconnected page rendered, no captured console warnings/errors. Run and demo
  were not invoked. No localhost result was used as hardware evidence.

## Remaining hardware verification

This session controls a Mac. Before any changes, its already-running ADB server
reported no devices. The user confirmed they do not have the Quest with them.
The server and unrelated applications were left alone.

When the Quest is available, recover any existing session rather than creating a
replacement run. Coordinate exclusive ownership: release/cancel browser USB before
native ADB inspection, then stop the ADB server before browser retries. Use the
same HTTPS origin and its persisted browser key. Do not clear storage, revoke all
keys, uninstall runners or replace drivers speculatively.

Check actual public-key `packet_write_complete` (AUTH subtype 3), incoming
`packet_received` CNXN, then `authenticate_complete`. A prepared key or approval
message alone proves none of these. Saved-key signature authentication may connect
without a public-key write. A CNXN event without `authenticate_complete` isolates
finalization; a write-start without write-complete isolates transfer consumption.

Still pending: verified Quest connection, exact signed runner install/reuse,
native capability inspection, GPU execution, recovery/download/upload of the
original native report, and report correctness/failure inspection. Keep any
benchmark failures separate from transport verification. Do not claim a hardware
connection fix from the deployed software tests.

---

## Historical Windows handoff (superseded by current status above)

The following preserves the previous investigation and its observations. Its
Git-only authorization note described that earlier handoff, not this authorized
repair/deployment pass.

# Handoff: Quest 3 stalls on the live website

Updated 2026-10-27. **Unresolved: no native benchmark execution was verified.**

## Goal and current state

The user has a Quest 3 connected by USB and wants diagnostics to run on
<https://xrbench.mickirusu.chatgpt.site>, not a demo or a localhost substitute.
The live page detects the Quest, but remains at browser USB authorization.
The user repeatedly reported approving the headset prompt. After deploying a
longer approval window, the user still reported "Still waiting for approval."
Do not treat this as solved or assume the user simply forgot to approve.

The last visible live message was:

> Allow USB debugging inside the headset for this website · waiting up to 3 minutes…

The user then pressed physical Escape to stop Computer Use. No more browser or
headset interaction was performed for this Git handoff. Resume hardware/UI work
only as part of a subsequent user-authorized debugging task.

## Repository state

- This handoff branch is based on GitHub `origin/main` at
  `f256735714d5fdd8c0f3b34335bc0bc7b8f7df19`.
- Continue from branch `usb-connection-handoff`.
- It carries only this investigation's transport progress, timeout changes,
  regression tests, and this document. The connection problem is not fixed yet.
- The original checkout is `C:/Users/micki/OneDrive/Desktop/xrBench`. It was on
  the older `72151d8` commit with extensive pre-existing tracked/untracked work
  on native operators, fixtures, installation recovery, and tests. Those local
  changes were preserved, not swept into this handoff commit.
- Read `AGENTS.md` and preserve others' work. If the task expands into device
  onboarding/qualification, follow `docs/workflows/device-onboarding.md`.

## Observations, not assumptions

- Windows reported `Reality Labs Composite ADB Interface` and XRSP with status
  `OK`. This alone does not prove the browser transport is working.
- Native ADB successfully listed one authorized device and returned model
  `Quest 3`, SoC `SXR2230P`, power connected, and wakefulness `Awake`.
- ADB was stopped after each inspection to release USB. Do not start an ADB
  server while expecting the browser to own the same USB interface.
- The new browser instrumentation progressed past `device.connect()` and
  credential access to the public-key authenticator. The approval label is
  emitted just before yielding its public-key packet; it does **not** prove
  delivery, device acceptance, receipt of `CNXN`, or completed authentication.
- One 60-second timeout preceded the user's approval response. Increasing the
  window was reasonable, but it did not resolve the later reported stall.
- Native ADB and the website have separate keys. Native ADB authorization does
  not establish browser authorization.
- `pm path` for the exact signed website package produced no path during the
  inspection. No website runner install or native benchmark was verified.
- No physical-device performance, correctness, or support claim can be made.

## Changes already made

`src/bench_web/web/browser/transport.mjs` now reports opening USB, waiting for
the handshake, accessing the browser credential, and requesting headset
approval. It distinguishes `usb_timeout`, `credential_timeout`, and
`approval_timeout`. USB setup retains its 60-second deadline; the public-key
approval stage starts a separate 180-second window.

`src/bench_web/web/browser/controller.mjs` passes progress into the session
message and requires explicit retry for the new timeout categories.

`src/bench_web/tests/test_browser_transport.mjs` covers the four timeout stages
and checks that the former one-minute deadline does not close an approval
request. These are simulated transport tests, not hardware evidence.

## The live Site is a separate repository

Do not overwrite the deployed bundle with an arbitrary build of the dirty
original checkout. The hosted source contains other diagnostics, recovery,
upload behavior, and signed-runner changes that must be preserved.

- Sites project: `appgprj_6ae0ddb34b808191af7c1f31adeb4ee1`
- Last successful deployed source commit:
  `48f9955a6ef24bcc0ab2ad7623d0610126908a7d`
- Deployment: `appgdep_6ae135cc5dc08191be7e2c5b4161869c`
- Saved version:
  `appgprj_6ae0ddb34b808191af7c1f31adeb4ee1~appgver_fb57ef107da481918cf3fce84700230e`
- Published October 27 at 7:37 PM America/New_York; native tool reported
  `succeeded`. Browser inspection later confirmed the three-minute message.
- Audience was owner-only. Preserve access and runtime secrets.
- Local hosted checkout:
  `C:/Users/micki/OneDrive/Desktop/xrBench/artifacts/site-usb-fix`
- Read the Sites skill, call `get_site`, renew a source repository credential,
  and use its source helper to open this existing project if that checkout is
  unavailable. Never create a replacement Site. Credentials expire; do not
  record tokens in files, command arguments, Git, or handoff notes.

The current signed manifest names package
`org.xrgpu.benchmarks.web.s975d199beb1b55fa3a729385b481616a7b36f3e919f0e2d74a9f3825d864e79a`,
version code 12, with APK SHA-256
`7d18fc075560f0b2584966fda263f4796ad80f5a45865e7c56ae480e8a8f0fc7`.
Use the actual manifest package, not just `org.xrgpu.benchmarks.web`.
Preserve these signed runner bytes during a browser-connection repair.

The hosted repository also contains a build correction: Windows CRLF caused
the exact Worker import replacement to fail; it now normalizes that source.
An intermediate deployment had stale subresource-integrity hashes and Chrome
blocked its JS/CSS. This was corrected and redeployed. `scripts/build.mjs`
now recalculates JS/CSS SRI and site metadata, and `tests/integrity.mjs` checks
the actual asset bytes. Always rebuild before checks/package after asset edits.

Ignored, machine-local investigation artifacts in the original checkout:

- `artifacts/patch-site-usb.mjs`: narrowly ports the connect method into the
  existing compiled Site bundle. It depends on exact symbols in the captured
  baseline; do not blindly reuse it against a different Site revision.
- `artifacts/site-usb-fix-app-readable.js`: readable baseline bundle.
- `artifacts/site-usb-approval.tar.gz`: last deployed archive.
- `engine/.state/usb-connection-20261027/evidence.json`: interim evidence.
  Its current-gate wording predates the final user report; this document's
  unresolved status takes precedence. These ignored artifacts are not in GitHub.

## Validation recorded before handoff

On the clean handoff branch based on current `origin/main`, this command passed
all **48 tests**, including the lifecycle-cancellation test mentioned below:

```powershell
node --test src/bench_web/tests/test_browser_transport.mjs src/bench_web/tests/test_browser_controller.mjs
```

`git diff --check` also passed. The managed checkout is
`C:/Users/micki/.codex/worktrees/usb-connection-handoff/xrBench`.
Its ignored `node_modules` junction reuses the original checkout's installed
dependencies; a different machine should install dependencies normally.

From the original dirty checkout:

```powershell
node --test src/bench_web/tests/test_browser_transport.mjs
node --test --test-name-pattern='USB|busy|permission|connection|recovery reserves' src/bench_web/tests/test_browser_controller.mjs src/bench_web/tests/test_browser_transport.mjs
```

These passed 18 and 13 tests respectively. An earlier combined controller and
transport run passed 45/46; the existing `headset lifecycle cancellation
preserves results and explains how to rerun` test expected `Keep it awake`,
while the current local controller returned `Canceled by user or activity
lifecycle Partial results are saved.` The connection patch does not touch that
wording. No full native/repository suite was claimed to pass.

In the hosted checkout, `node scripts/build.mjs` and
`node --test tests/*.mjs` passed (12 hosted tests, including asset integrity).
Re-run relevant tests against the exact branch/source you continue from.

## Recommended next investigation

1. Recover the current Site source and inspect the live page state. Preserve
   saved sessions/reports; recover an already launched run rather than launching
   a duplicate. The last observed session was still pre-installation.
2. Trace the authentication boundary in the installed Tango libraries:
   `@yume-chan/adb` 2.6.4, `adb-daemon-webusb` 2.3.2,
   `adb-credential-web` 2.1.0, and `stream-extra` 2.6.1. Log packet command/type,
   stage transitions, and elapsed times only, never RSA key bytes or AUTH
   payloads. Establish whether the public-key write completes, whether `CNXN`
   arrives, and whether `authenticate()` returns or stalls while releasing its
   stream locks. None of these failure hypotheses has been confirmed.
3. Obtain focused, recent Android authorization logs if needed. Coordinate USB
   ownership: cancel/release the browser before native ADB, and stop the ADB
   server before retrying the browser. The installed executable used was
   `C:/Program Files/Unity/Hub/Editor/6000.3.10f1-x86_64/Editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb.exe`.
   Keep device serials and raw logs in ignored evidence storage.
4. Check cancellation/late-authentication behavior: current timeout handling
   closes raw USB and relies on the library promise to reject. The regression
   tests simulate that rejection; they do not prove a hung library operation
   cannot outlive the deadline. Also avoid telling a recovering run that no
   benchmark ever started merely because its reconnect timed out.
5. Do not clear site storage, revoke all debugging keys, uninstall runners,
   replace USB drivers, or kill unrelated applications as speculative fixes.
   Do not keep asking the user to repeat the same approval without new evidence.
6. Verify the final repair on the actual HTTPS Site and headset: authentication,
   matching runner install/reuse, capabilities, native launch, progress, original
   report recovery, and upload/download behavior. Software tests and catalog
   recognition cannot substitute for this check.

Browser automation became unreliable near the end (CDP timeouts and an input
target mismatch). A `chrome://device-log/` navigation was blocked by browser
policy; do not circumvent that block through another control surface. The user
eventually stopped Computer Use. This handoff request authorizes Git work only.
