# Bench Web

The default website uses locally bundled Three.js for a procedural headset view.
Run, Stop, progress, and outcomes use the shared engine HTTP API. Browser graphics
do not execute layer workloads. HTML controls remain available without WebGL.

Run `npm ci && npm run build` from the repository root after a fresh checkout or
scene change. The rest of the interface is buildless HTML/CSS/JavaScript.
