# Bench Web

The default website contains a locally bundled Three.js headset hero and a live
results section that appears after starting or selecting a run. The Run button
beneath the hero description starts the full isolated smoke
suite on the single connected, authorized headset, then becomes Stop while a run
is active. Progress and setup messages appear below Run, hidden when idle;
the live results section groups all 63 cases into 25 layer types. Expand a layer
to see input shapes, correctness status, sample count, median/p95 execution latency,
and GPU timestamps when available. Failed, unsupported, interrupted, and invalid
timings remain explicit; missing values never become zero. Detailed settings,
device selection, comparisons, and downloads live in Stats for Nerds. Both views
retain the selected run. Results and progress refresh once per second, including
after reconnecting; polling never launches another benchmark.

Browser graphics do not execute layer workloads. The Run control remains
available without WebGL. Missing or multiple headsets receive setup guidance.

The hero detects devices through the local ADB service (developer mode and USB
debugging are required). Host device discovery refreshes every three seconds. It shows a connection prompt
until a device is identified. Quest 3 receives a locally built, procedural model;
other models are identified without substituting an inaccurate Quest preview.
Unauthorized Quest 3 devices can be previewed while awaiting the debugging prompt.
Benchmark eligibility remains independent of preview availability.

Drag to rotate, scroll or pinch to zoom, and use Reset view to return home. Focus
the model to use arrow keys, +/−, and Home. The headset is built from ivory beveled
blocks with graphite cushioning and three dark front sensor windows. Detection
plays a 3.2-second exploded assembly: recognizable shell, cushion/optics, sensor
columns, and strap components fade in while suspended apart. The shell and cushion
seat first, followed by the sensors and straps, with a restrained overshoot and
staggered cube settling. A brief sensor glint and ring pulse finish the reveal,
then the headset settles into gentle idle motion once authorized. Status-node
colors remain tied to benchmark results throughout. The shared
cube geometry is rendered in four instanced batches; no interior shell cubes are
drawn.

Assembly plays when a recognized Quest 3 first appears or reconnects, including
while awaiting USB authorization. Polling and authorization changes do not replay
it. The camera begins about 20% wider, with additional clearance on narrow screens,
and arcs 12 degrees into the home view. Rotating or zooming immediately takes over
the camera, continues assembly, and pauses subsequent idle motion;
Reset view completes assembly and restores the home camera without replaying it.
Reduced motion shows the completed model immediately without camera motion or
finishing effects. Hidden pages and WebGL context loss pause the entire reveal's
animation time. Unavailable WebGL falls back to text while
leaving benchmark controls usable.

The original model in `src/quest3.js` uses the standard soft strap and exterior
references from https://www.meta.com/quest/quest-3/. It is illustrative, not CAD
or a hardware telemetry display. No models, textures, or fonts load remotely.

Run `npm ci && npm run build` from the repository root after a fresh checkout or
scene change. The rest of the interface is buildless HTML/CSS/JavaScript.
