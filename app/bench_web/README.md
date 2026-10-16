# Bench Web

The default website contains only the header and a locally bundled Three.js headset
hero. The Run button beneath the hero description starts the full isolated smoke
suite on the single connected, authorized headset, then becomes Stop while a run
is active. Progress and setup messages appear below Run, hidden when idle;
detailed settings, device selection, results, and downloads live in Stats for
Nerds. Both views retain the selected run.

Browser graphics do not execute layer workloads. The Run control remains
available without WebGL. Missing or multiple headsets receive setup guidance.

The hero detects devices through the local ADB service (developer mode and USB
debugging are required), polling every three seconds. It shows a connection prompt
until a device is identified. Quest 3 receives a locally built, procedural model;
other models are identified without substituting an inaccurate Quest preview.
Unauthorized Quest 3 devices can be previewed while awaiting the debugging prompt.
Benchmark eligibility remains independent of preview availability.

Drag to rotate, scroll or pinch to zoom, and use Reset view to return home. Focus
the model to use arrow keys, +/−, and Home. Authorization plays a brief entrance
followed by gentle idle motion; interaction pauses motion until reset. Reduced
motion disables automatic animation. Hidden pages pause rendering, and unavailable
WebGL falls back to text while leaving benchmark controls usable.

The original model in `src/quest3.js` uses the standard soft strap and exterior
references from https://www.meta.com/quest/quest-3/. It is illustrative, not CAD
or a hardware telemetry display. No models, textures, or fonts load remotely.

Run `npm ci && npm run build` from the repository root after a fresh checkout or
scene change. The rest of the interface is buildless HTML/CSS/JavaScript.
