const GLYPHS = '.,:;=+*#%@';
const REST_POSE = {yaw: -.55, pitch: .28, roll: -.055};
const FRAME_MS = 1000 / 30;
const normalize = (x, y, z) => { const length = Math.hypot(x, y, z) || 1; return [x / length, y / length, z / length]; };

// Indexed surfaces are built once. Each vertex holds position, normal and albedo.
export function createHeadsetMesh(includeChip = false) {
  const vertices = [], triangles = [], parts = [];
  let part = 0;
  function surface(columns, rows, point, albedo) {
    const start = vertices.length / 7;
    for (let row = 0; row <= rows; row++) for (let col = 0; col <= columns; col++) {
      const [position, normal] = point(col / columns, row / rows);
      vertices.push(position[0], position[1] - .25, position[2] + .35, ...normal, albedo);
      parts.push(part);
    }
    for (let row = 0; row < rows; row++) for (let col = 0; col < columns; col++) {
      const a = start + row * (columns + 1) + col, b = a + 1, c = a + columns + 1;
      triangles.push(a, b, c, b, c + 1, c);
    }
  }
  function roundedBox(center, size, radii, albedo, bend = 0) {
    const half = size.map(value => value / 2), core = half.map((value, i) => value - radii[i]);
    for (let axis = 0; axis < 3; axis++) for (const sign of [-1, 1]) {
      const u = (axis + 1) % 3, v = (axis + 2) % 3;
      surface(size[0] > 2 ? 24 : 8, size[0] > 2 ? 18 : 6, (s, t) => {
        const point = [0, 0, 0]; point[axis] = sign * half[axis];
        point[u] = (s * 2 - 1) * half[u]; point[v] = (t * 2 - 1) * half[v];
        const inner = point.map((value, i) => Math.max(-core[i], Math.min(core[i], value)));
        const radial = normalize(...point.map((value, i) => (value - inner[i]) / radii[i]));
        const position = inner.map((value, i) => value + radial[i] * radii[i]);
        const normal = radial.map((value, i) => value / radii[i]);
        normal[0] += 2 * bend * position[0] / (half[0] ** 2) * normal[2];
        position[2] -= bend * (position[0] / half[0]) ** 2;
        return [position.map((value, i) => value + center[i]), normalize(...normal)];
      }, albedo);
    }
  }
  function lens(x, y, z) {
    // A raised circular bezel, recessed glass, and a small off-center highlight.
    surface(24, 3, (u, v) => {
      const angle = u * Math.PI * 2, radius = .058 + v * .033;
      return [[x + Math.cos(angle) * radius, y + Math.sin(angle) * radius, z + .012 * Math.sin(v * Math.PI)],
        normalize(Math.cos(angle) * .4, Math.sin(angle) * .4, 1)];
    }, .68);
    surface(24, 2, (u, v) => {
      const angle = u * Math.PI * 2, radius = v * .058;
      return [[x + Math.cos(angle) * radius, y + Math.sin(angle) * radius, z - .004], [0, 0, 1]];
    }, .08);
    roundedBox([x - .018, y + .021, z + .006], [.025, .027, .012], [.011, .012, .005], 1);
  }
  // A curved front shell, an inset face, and a darker rear facial gasket.
  roundedBox([0, -.12, .57], [3.2, 1.52, 1.05], [.46, .35, .27], .9, .14);
  roundedBox([0, -.12, 1.04], [3.055, 1.375, .17], [.45, .34, .075], .42, .13);
  roundedBox([0, -.12, 1.075], [2.98, 1.30, .16], [.43, .32, .07], .98, .13);
  part = 1;
  roundedBox([0, -.12, -.025], [3.06, 1.39, .28], [.42, .32, .12], .35, .07);
  part = 0;
  // Three camera pods make the solid front visor read as an XR headset.
  for (const x of [-.92, 0, .92]) {
    const z = 1.18 - .13 * (x / 1.49) ** 2;
    roundedBox([x, -.10, z], [.23, .58, .11], [.11, .20, .045], .18);
    lens(x, .035, z + .062); lens(x, -.235, z + .062);
  }
  // Recessed cooling slots and the small control on the upper shell.
  for (let i = -4; i <= 4; i++) roundedBox([i * .18, .642, .57], [.075, .024, .23], [.03, .01, .06], .25);
  roundedBox([.96, .625, .51], [.30, .055, .16], [.07, .024, .06], .62);
  part = 1;
  // Side attachments join the visor to the wraparound band.
  for (const side of [-1, 1]) roundedBox([side * 1.59, -.03, -.1], [.27, .49, .62], [.11, .18, .15], .72);
  // A closed rectangular band around the back of the head, open in the middle.
  for (const side of [-1, 1]) {
    surface(48, 2, (u, v) => {
      const angle = u * Math.PI, radius = 1.61 + side * .065;
      return [[radius * Math.cos(angle), .08 + (v - .5) * .35, -.14 - radius * Math.sin(angle)],
        [side * Math.cos(angle), 0, -side * Math.sin(angle)]];
    }, .76);
    surface(48, 2, (u, v) => {
      const angle = u * Math.PI, radius = 1.61 + (v - .5) * .13;
      return [[radius * Math.cos(angle), .08 + side * .175, -.14 - radius * Math.sin(angle)], [0, side, 0]];
    }, .8);
  }
  // A second ribbon arches over the crown; its hollow center stays transparent.
  function crown(t) { return [.51 + .91 * Math.sin(t * Math.PI) - .36 * t, .2 - 2 * t]; }
  function crownNormal(t) { const dy = .91 * Math.PI * Math.cos(t * Math.PI) - .36; return normalize(0, 2, dy); }
  for (const side of [-1, 1]) {
    surface(40, 2, (u, v) => {
      const [y, z] = crown(u), normal = crownNormal(u);
      return [[(v - .5) * .36, y + side * .045 * normal[1], z + side * .045 * normal[2]], normal.map(n => n * side)];
    }, .87);
    surface(40, 2, (u, v) => {
      const [y, z] = crown(u), normal = crownNormal(u);
      return [[side * .18, y + (v - .5) * .09 * normal[1], z + (v - .5) * .09 * normal[2]], [side, 0, 0]];
    }, .65);
  }
  // Raised stitched edges follow the crown's curve, preserving its hollow shape.
  for (const side of [-1, 1]) surface(48, 2, (u, v) => {
    const [y, z] = crown(u), normal = crownNormal(u);
    return [[side * .145 + (v - .5) * .018, y + .052 * normal[1], z + .052 * normal[2]], normal];
  }, .38);
  // Rear padding and the overlapping strap keeper give the band visible thickness.
  roundedBox([0, .08, -1.75], [.72, .44, .18], [.13, .12, .08], .42);
  roundedBox([0, .19, -1.73], [.49, .19, .24], [.055, .055, .075], .74);
  if (includeChip) {
    part = 2;
    // A package, raised die rim, and pins; the diagnostic surface is inside the rim.
    roundedBox([0, .25, -.35], [4, 3.3, .26], [.12, .12, .10], .52);
    for (const side of [-1, 1]) {
      roundedBox([side * 1.85, .25, -.16], [.12, 3.08, .12], [.04, .06, .04], .85);
      roundedBox([0, .25 + side * 1.49, -.16], [3.7, .12, .12], [.06, .04, .04], .85);
      for (let i = -7; i <= 7; i++) {
        roundedBox([side * 2.10, .25 + i * .195, -.40], [.28, .075, .08], [.025, .025, .025], .7);
        roundedBox([i * .24, .25 + side * 1.75, -.40], [.09, .26, .08], [.025, .025, .025], .7);
      }
    }
    for (const [index, x] of [-.89, .89].entries()) {
      part = index + 3;
      roundedBox([x, .25, -.13], [1.60, 2.70, .10], [.06, .06, .04], .8);
    }
  }
  return {vertices: new Float32Array(vertices), triangles: new Uint32Array(triangles), parts: new Uint8Array(parts)};
}

// Perspective projection and a reciprocal-depth buffer, all rendered as text.
// No canvas or WebGL context is created; mesh projection runs in JavaScript.
export function createAsciiRenderer(mesh = createHeadsetMesh(true)) {
  const projected = new Float32Array(mesh.vertices.length / 7 * 4);
  let columns = 0, rows = 0, depth, characters, lines, chipFace = null;
  function resize(width, height) {
    if (columns === width && rows === height) return;
    columns = width; rows = height;
    depth = new Float32Array(columns * rows); characters = new Uint8Array(columns * rows);
    lines = new Array(rows);
  }
  function render({yaw = REST_POSE.yaw, pitch = REST_POSE.pitch, roll = REST_POSE.roll, cellAspect = .5, reveal = 0, chipRatio = 1, faceColumns = columns * .7, processor = 'cpu'} = {}) {
    if (!columns || !rows) return '';
    depth.fill(0); characters.fill(32);
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cx = Math.cos(pitch), sx = Math.sin(pitch), cz = Math.cos(roll), sz = Math.sin(roll);
    // Yaw, then pitch, then roll. The same orthonormal matrix rotates normals.
    const m = [cz * cy - sz * sx * sy, -sz * cx, cz * sy + sz * sx * cy,
      sz * cy + cz * sx * sy, cz * cx, sz * sy - cz * sx * cy,
      -cx * sy, sx, cx * cy];
    const smooth = value => {const t = Math.max(0, Math.min(1, value)); return t * t * (3 - 2 * t);};
    const spread = smooth(reveal / .6), flight = smooth((reveal - .2) / .8);
    const chipScale = .08 + .92 * flight, chipYaw = yaw * (1 - flight), chipPitch = pitch * (1 - flight);
    const cc = Math.cos(chipYaw), cs = Math.sin(chipYaw), pc = Math.cos(chipPitch), ps = Math.sin(chipPitch);
    const chipMatrix = [cc, 0, cs, ps * cs, pc, -ps * cc, -pc * cs, ps, pc * cc];
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    chipFace = null;
    for (let i = 0, j = 0; i < mesh.vertices.length; i += 7, j += 4) {
      const data = mesh.vertices;
      const x = data[i], y = data[i + 1], z = data[i + 2], nx = data[i + 3], ny = data[i + 4], nz = data[i + 5], albedo = data[i + 6];
      const part = mesh.parts?.[i / 7] || 0, chip = part >= 2;
      if (chip && !reveal) continue;
      // Framing follows the original headset, then the chip, never the receding parts.
      const originalZ = m[6] * x + m[7] * y + m[8] * z;
      if (!chip) {
        const ox = (m[0] * x + m[1] * y + m[2] * z) * 6 / (6 - originalZ);
        const oy = (m[3] * x + m[4] * y + m[5] * z) * 6 / (6 - originalZ);
        minX = Math.min(minX, ox); maxX = Math.max(maxX, ox);
        minY = Math.min(minY, oy); maxY = Math.max(maxY, oy);
      }
      const matrix = chip ? chipMatrix : m, scale = chip ? chipScale : 1 - .58 * flight;
      const vx = x * scale, vy = y * scale * (chip ? 1 + (chipRatio - 1) * flight : 1), vz = z * scale;
      const px = matrix[0] * vx + matrix[1] * vy + matrix[2] * vz + (chip ? 0 : (part === 0 ? -2.9 : 2.9) * spread);
      const py = matrix[3] * vx + matrix[4] * vy + matrix[5] * vz + (chip ? 0 : .8 * spread);
      const pz = matrix[6] * vx + matrix[7] * vy + matrix[8] * vz + (chip ? flight : -1.6 * spread), inverse = 1 / (6 - pz);
      const light = Math.max(0, -.44 * (matrix[0] * nx + matrix[1] * ny + matrix[2] * nz)
        + .72 * (matrix[3] * nx + matrix[4] * ny + matrix[5] * nz) + .53 * (matrix[6] * nx + matrix[7] * ny + matrix[8] * nz));
      projected[j] = px * 6 * inverse;
      projected[j + 1] = py * 6 * inverse;
      projected[j + 2] = inverse;
      const activeRegion = part === (processor === 'gpu' ? 4 : 3);
      projected[j + 3] = (1 - albedo * (.27 + .73 * light)) * (chip ? (activeRegion ? .65 : 1) : 1 - .8 * flight);
    }
    // Fit the projected geometry closely; margins protect the rotating silhouette.
    const idleScale = Math.min((columns - 6) / (maxX - minX || 1), (rows - 4) / ((maxY - minY || 1) * cellAspect));
    const finalScale = faceColumns / (3.44 * 6 / 4.83);
    const scale = idleScale * (1 - flight) + finalScale * flight;
    const centerX = (minX + maxX) / 2 * (1 - flight), centerY = (minY + maxY) / 2 * (1 - flight);
    for (let j = 0; j < projected.length; j += 4) {
      projected[j] = columns / 2 + (projected[j] - centerX) * scale;
      projected[j + 1] = rows / 2 - (projected[j + 1] - centerY) * scale * cellAspect;
    }
    if (reveal > 0) {
      const face = [];
      for (const x of [-1.72, 1.72]) for (const y of [-1.32, 1.32]) {
        const vx=x*chipScale, vy=y*chipScale*(1+(chipRatio-1)*flight), vz=.17*chipScale;
        const px=chipMatrix[0]*vx+chipMatrix[2]*vz, py=chipMatrix[3]*vx+chipMatrix[4]*vy+chipMatrix[5]*vz;
        const z=chipMatrix[6]*vx+chipMatrix[7]*vy+chipMatrix[8]*vz+flight;
        face.push([columns/2+(px*6/(6-z)-centerX)*scale, rows/2-(py*6/(6-z)-centerY)*scale*cellAspect]);
      }
      const left=Math.min(...face.map(p=>p[0])), right=Math.max(...face.map(p=>p[0]));
      const top=Math.min(...face.map(p=>p[1])), bottom=Math.max(...face.map(p=>p[1]));
      chipFace={left,top,width:right-left,height:bottom-top};
    }
    for (let i = 0; i < mesh.triangles.length; i += 3) {
      if (!reveal && (mesh.parts?.[mesh.triangles[i]] || 0) >= 2) continue;
      const a = mesh.triangles[i] * 4, b = mesh.triangles[i + 1] * 4, c = mesh.triangles[i + 2] * 4;
      const ax = projected[a], ay = projected[a + 1], bx = projected[b], by = projected[b + 1], cx = projected[c], cy = projected[c + 1];
      const area = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
      if (Math.abs(area) < 1e-6) continue;
      const left = Math.max(0, Math.floor(Math.min(ax, bx, cx))), right = Math.min(columns - 1, Math.ceil(Math.max(ax, bx, cx)));
      const top = Math.max(0, Math.floor(Math.min(ay, by, cy))), bottom = Math.min(rows - 1, Math.ceil(Math.max(ay, by, cy)));
      for (let row = top; row <= bottom; row++) for (let col = left; col <= right; col++) {
        const u = ((by - cy) * (col + .5 - cx) + (cx - bx) * (row + .5 - cy)) / area;
        const v = ((cy - ay) * (col + .5 - cx) + (ax - cx) * (row + .5 - cy)) / area, w = 1 - u - v;
        if (u < 0 || v < 0 || w < 0) continue;
        const inverse = u * projected[a + 2] + v * projected[b + 2] + w * projected[c + 2], index = row * columns + col;
        if (inverse <= depth[index]) continue;
        depth[index] = inverse;
        const shade = u * projected[a + 3] + v * projected[b + 3] + w * projected[c + 3];
        characters[index] = GLYPHS.charCodeAt(Math.max(0, Math.min(GLYPHS.length - 1, Math.round(shade * (GLYPHS.length - 1)))));
      }
    }
    for (let row = 0; row < rows; row++) lines[row] = String.fromCharCode(...characters.subarray(row * columns, (row + 1) * columns));
    return lines.join('\n');
  }
  return {resize, render, get chipFace() {return chipFace;}};
}

// Pointer origin stays fixed even when the rendered headset moves.
export function avoidanceOffset(x, y, bounds, art) {
  const dx = x - (bounds.left + bounds.width / 2), dy = y - (bounds.top + bounds.height / 2);
  const distance = Math.hypot(dx, dy), strength = Math.max(0, 1 - distance / 240);
  const limitX = Math.max(0, Math.min(54, (bounds.width - art.width) / 2 - 12));
  const limitY = Math.max(0, Math.min(32, (bounds.height - art.height) / 2 - 12));
  return {x: -(distance ? dx / distance : 1) * limitX * strength, y: -(distance ? dy / distance : 0) * limitY * strength};
}

export function mountHeadset(stage, win = window, createRenderer = createAsciiRenderer) {
  const art = stage.querySelector('pre'), panel = stage.querySelector('#chip-dashboard'), doc = stage.ownerDocument;
  const reduced = win.matchMedia?.('(prefers-reduced-motion: reduce)');
  const fine = win.matchMedia?.('(hover: hover) and (pointer: fine)');
  let renderer, failed = false;
  let frame = null, previous = null, elapsed = 0, destroyed = false, inView = true;
  let mode = 'headset', key = '', source = 'native', processor = 'cpu', reveal = 0;
  let x = 0, y = 0, target = {x: 0, y: 0}, cellAspect = .5, needsSize = true;
  let columns = 160, rows = 53, cellWidth = 1, lineHeight = 2, width = 0, height = 0, faceWidth = 0, faceHeight = 0;
  art.setAttribute('aria-hidden', 'true'); stage.dataset.mode = mode;
  const visible = () => !destroyed && !doc.hidden && inView;
  const idle = () => mode === 'headset' && !!fine?.matches && !reduced?.matches;
  const animating = () => typeof win.requestAnimationFrame === 'function' && !failed && (idle() || mode === 'chip' && reveal < 1 && !reduced?.matches);
  function cancel() {
    if (frame !== null) win.cancelAnimationFrame(frame);
    frame = null; previous = null;
  }
  function fallback() {
    failed = true; cancel(); stage.dataset.graphics = 'fallback'; art.textContent = ''; stage.style.height = '';
    if (panel) for (const name of ['left', 'top', 'transform', 'opacity']) panel.style[name] = '';
  }
  try { renderer = createRenderer(); } catch { fallback(); }
  function size() {
    let bounds = stage.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return false;
    faceWidth = panel?.offsetWidth || Math.min(680, Math.max(180, bounds.width - 72));
    faceHeight = panel?.offsetHeight || 340;
    if (mode === 'chip') {
      const nextHeight = Math.ceil(Math.max(430, faceHeight * 1.43 + 40)) + 'px';
      if (stage.style.height !== nextHeight) stage.style.height = nextHeight;
      bounds = stage.getBoundingClientRect();
    }
    width = bounds.width; height = bounds.height;
    columns = width < 380 ? 100 : width < 500 ? 128 : width > 800 ? 200 : 160;
    rows = Math.max(24, Math.min(180, Math.round(columns * .5 * (height - 48) / Math.max(1, width - 48))));
    const fontSize = Math.min(9, (width - 48) / (columns * .61), (height - 48) / (rows * 1.2));
    art.style.fontSize = Math.max(2, fontSize).toFixed(3) + 'px';
    const styles = win.getComputedStyle(art);
    lineHeight = styles.lineHeight.endsWith('px') ? parseFloat(styles.lineHeight) : fontSize * (parseFloat(styles.lineHeight) || 1.2);
    renderer.resize(columns, rows);
    art.textContent = '0'.repeat(columns);
    cellWidth = art.getBoundingClientRect().width / columns || fontSize * .6;
    cellAspect = cellWidth / lineHeight;
    needsSize = false; return true;
  }
  function paint() {
    if (failed) return;
    try {
      if (needsSize && !size()) return;
      const sweep = idle() ? Math.sin(elapsed / 8000) : 0;
      art.textContent = renderer.render({yaw: REST_POSE.yaw + sweep * .30 + x * .002,
        pitch: REST_POSE.pitch + (idle() ? Math.sin(elapsed / 11000) * .035 : 0) - y * .0015,
        cellAspect, reveal, chipRatio: faceHeight / faceWidth * 3.44 / 2.64, faceColumns: faceWidth / cellWidth, processor});
      art.style.transform = x || y ? `translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0)` : '';
      stage.dataset.transition = mode === 'chip' && reveal < 1 ? 'opening' : 'settled';
      const face = renderer.chipFace;
      if (mode === 'chip' && panel && face) {
        panel.style.left = (width / 2 + (face.left + face.width / 2 - columns / 2) * cellWidth) + 'px';
        panel.style.top = (height / 2 + (face.top + face.height / 2 - rows / 2) * lineHeight) + 'px';
        panel.style.transform = `translate(-50%, -50%) scale(${face.width * cellWidth / faceWidth})`;
        panel.style.opacity = String(Math.max(0, Math.min(1, (reveal - .55) / .25)));
      } else if (mode === 'chip' && panel) panel.style.opacity = '0';
    } catch { fallback(); }
  }
  function schedule() { if (frame === null && visible() && animating()) frame = win.requestAnimationFrame(animate); }
  function animate(now) {
    frame = null;
    if (!visible() || !animating()) { previous = null; return; }
    if (previous === null || now - previous >= FRAME_MS) {
      const dt = previous === null ? 0 : Math.min(now - previous, 100);
      elapsed += dt; previous = now;
      if (mode === 'chip') reveal = Math.min(1, reveal + dt / 1500);
      const easing = 1 - Math.exp(-dt / 100);
      x += (target.x - x) * easing; y += (target.y - y) * easing;
      if (Math.abs(x - target.x) + Math.abs(y - target.y) < .1) { x = target.x; y = target.y; }
      paint();
    }
    schedule();
  }
  function sync() {
    if (!visible()) { cancel(); return; }
    if (!idle()) { elapsed = 0; x = y = 0; target = {x: 0, y: 0}; }
    if (reduced?.matches && mode === 'chip') reveal = 1;
    if (!animating()) cancel();
    paint(); schedule();
  }
  function resize() { needsSize = true; sync(); }
  function move(event) {
    if (!visible() || !idle() || event.pointerType === 'touch') return;
    target = avoidanceOffset(event.clientX, event.clientY, stage.getBoundingClientRect(), art.getBoundingClientRect());
  }
  function leave() { target = {x: 0, y: 0}; }
  const intersection = win.IntersectionObserver ? new win.IntersectionObserver(entries => {
    const entry = entries.find(item => item.target === stage);
    if (entry && inView !== entry.isIntersecting) { inView = entry.isIntersecting; sync(); }
  }) : null;
  const resizer = win.ResizeObserver ? new win.ResizeObserver(resize) : null;
  intersection?.observe(stage); resizer?.observe(stage); if (panel) resizer?.observe(panel);
  if (!resizer) win.addEventListener('resize', resize);
  doc.addEventListener('visibilitychange', sync);
  stage.addEventListener('pointermove', move); stage.addEventListener('pointerleave', leave);
  reduced?.addEventListener('change', sync); fine?.addEventListener('change', sync);
  sync();
  return {
    setPresentation({mode: next = 'headset', runKey = '', processor: cpu = 'cpu', source: origin = 'native', animate = false} = {}) {
      if (destroyed) return;
      const changed = mode !== next || key !== runKey || source !== origin;
      if (!changed && processor === cpu) return;
      if (changed) {
        cancel(); reveal = next === 'chip' ? animate && !reduced?.matches && typeof win.requestAnimationFrame === 'function' ? 0 : 1 : 0;
        x = y = elapsed = 0; target = {x: 0, y: 0}; needsSize = true;
      }
      mode = next; key = runKey; source = origin; processor = cpu;
      stage.dataset.mode = mode; stage.dataset.runKey = key; stage.dataset.source = source;
      if (mode === 'headset') stage.style.height = '';
      sync();
    },
    destroy() {
      destroyed = true; cancel(); intersection?.disconnect(); resizer?.disconnect();
      win.removeEventListener('resize', resize); doc.removeEventListener('visibilitychange', sync);
      stage.removeEventListener('pointermove', move); stage.removeEventListener('pointerleave', leave);
      reduced?.removeEventListener('change', sync); fine?.removeEventListener('change', sync);
    },
  };
}
