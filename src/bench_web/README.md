# Bench Web

The local homepage keeps the ivory, graphite and sage palette. Run creates an
isolated Smoke session: all 63 standard CPU cases, followed by all 63 standard GPU
cases. The controller owns this sequence and reserves the selected device across
both phases. Browsers only observe native evidence. Stats for Nerds retains
single-backend controls, development workloads and custom kernel experiments.

## Device and processor identity

`engine/device-catalog.json` is the versioned source for detection, chip locations, vendor references and the supported-devices panel. Exact aliases
are matched against Android model, product and device fields. Native and Android
SoC reports are retained separately from catalog inference. Conflicting identities
are flagged for verification. Multiple connected devices require explicit selection;
Quest 3 is never selected preferentially.

Recognition does not imply execution compatibility or physical validation. Android
10+ arm64 devices require USB debugging and a native capability check. Qualcomm XR
families are candidates for the Android runners; Apple CPU/Metal is a separate
milestone. Proprietary spatial processors remain listed with SDK/runner limitations.
For Aura, Reality Elite is located in the compute puck and X1S in the glasses.
The progress panel identifies the component executing the benchmark.

## Live evidence

A compact device-status panel shows the selected device and processor. An accessible
HTML dashboard shows elapsed time, phase ETA, operation, correctness, samples and
recent validated results. Expandable case details retain each CPU and GPU case's
native status. The gauge is **Suite completed**, never utilization. Counts and
results come from native reports; unsupported backends do not inflate completion.
ETA uses recent completed case durations within the current processor phase.
Preparation shows “Estimating…”. Stale progress stops projecting activity and ETA.
Only the elapsed display clock is interpolated.

Finished sessions retain separate CPU/GPU summaries and reports on the homepage.
Run starts the existing native session immediately, then plays a 1.5-second ASCII
reveal: the visor and rear assembly separate and a chip flies forward. Readable
HTML diagnostics sit inside the projected chip outline. CPU/GPU regions follow
the current native phase; they are presentation regions, not a physical die map
or utilization measurement. Expanded evidence and case results stay below.

The scene uses a dependency-free software 3D renderer with depth testing, surface
lighting, curved visor, camera lenses, cooling slots, straps, and a chip package
with pins. The large, borderless artwork shares the ivory and sage hero canvas.
`mountHeadset()` returns `setPresentation({mode, runKey, processor, source, animate})`
and `destroy()`. A run key triggers its reveal once. Repeated progress updates
never replay it; restored runs go straight to the chip. Returning to the headset
preserves results, and a new run reveals its chip again.

Rendering is capped at 30 FPS and pauses offscreen or in hidden tabs. A settled
chip needs no continuous animation. Reduced motion skips the reveal. Touch has
no idle movement or cursor avoidance, but permits the user-triggered reveal.
Rendering failures retain readable diagnostics and Run/Stop controls. On phones,
the action row remains visible while scrolling through the chip.

Stats for Nerds is available only through its explicitly named navigation link.
Connection, USB authorization, unsupported hardware, service errors and missing
native APK guidance are handled inline with Retry. The local toolchain and built
APK remain host prerequisites; the homepage does not invoke builds. The existing
session controller installs the APK, checks capabilities, and runs CPU then GPU.

## Optional device demo

Choose **Try a demo**, pick any catalog device, and select **Connect demo device**.
Then **Run demo** previews the standard CPU and GPU case lists in about 12 seconds.
Stop, rerun, change devices between runs, or use **Exit demo** to restore the live
device view. Apple and specialized glasses retain their actual support notes;
successful simulation does not imply native compatibility.

To disable the entire demo, set `ENABLE_SMOKE_DEMO = false` in
`web/features.mjs` and reload. The demo controls are not mounted and
`web/smoke-demo.mjs` is not imported. No server option or dependency is required.

The component reads the existing device and workload catalogs but keeps all
synthetic connection, progress, and results state in memory. It never sends POST
requests, launches hardware workloads, saves reports, or puts demo IDs in URLs or
browser storage. All models share the same illustrative sample timings. A local
service is needed to serve the page and catalogs; a device and built APK are not.
Catalog failures offer retry. Native activity prevents demo entry and takes over
the view if a real run appears while the demo is open.

## Session API and recovery

- `POST /api/benchmark-sessions`: `{serial, request_id}`. Reusing the same request
  ID returns the original session. A device cannot be reserved by two sessions.
- `GET /api/benchmark-sessions/<id>`: phase state and existing child run identities.
- `POST /api/benchmark-sessions/<id>/stop`: durable cancellation, including between
  phases. Fatal failure or cancellation prevents the next phase.
- `GET /api/device-catalog`: shared identity catalog. Existing `/api/runs` routes
  and reports remain compatible.

All routes use the existing local session/origin protection. Session state lives in
`engine/.state/benchmark-sessions/`. `?session=<id>` restores browser selection.
Controller restart recovers persisted child identities; disconnects recover the
same native report. A terminal progress message alone cannot advance the session:
the controller must recover and validate the final report first. Setup failure or
a build/identity change is explicit and does not silently launch a replacement.

## Build and verify

From the repository root:

```sh
npm ci
npm test
uv run --extra export --group dev python -m layers.export
uv run --extra export --group dev python -m engine.verify_cpu
uv run --extra export --group dev python -m engine.build
uv run --group dev python -m pytest -q
uv run python -m src
```

Both web interfaces are buildless. Host CPU correctness
covers both the 63 standard and 17 development cases using the shared references.
Browser fixtures test presentation only and must not be submitted as qualification evidence. Quest 3 and a second Android
XR device remain required for physical qualification; neither was attached during
this implementation. See [hardware qualification](../../kernels/PORTABILITY.md).
