import * as THREE from './vendor/three.module.js';
import { VERT, FRAG, MODES } from './shaders.js';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  library: null,
  entry: null,
  modeKey: 'immersive',
  yaw: 0, pitch: 0, roll: 0,
  targetYaw: 0, targetPitch: 0,
  fov: 75, targetFov: 75,
  morph: 0,
  exposure: 1,
  globeDist: 2.6,
  autoSpin: false,
  spinSpeed: 0.05,
  dragging: false,
  lastX: 0, lastY: 0,
  velYaw: 0, velPitch: 0,
};

const DEG = Math.PI / 180;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const modeByKey = (k) => MODES.find((m) => m.key === k) || MODES[0];

// ---------------------------------------------------------------------------
// Renderer
// ---------------------------------------------------------------------------

const canvas = document.getElementById('stage');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));

const scene = new THREE.Scene();
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

const uniforms = {
  uPano: { value: null },
  uCamRot: { value: new THREE.Matrix3() },
  uAspect: { value: 1 },
  uFov: { value: 75 * DEG },
  uMode: { value: 0 },
  uMorph: { value: 0 },
  uLonRange: { value: new THREE.Vector2(-Math.PI, Math.PI) },
  uLatRange: { value: new THREE.Vector2(-Math.PI / 2, Math.PI / 2) },
  uExposure: { value: 1 },
  uGlobeDist: { value: 2.6 },
  // The shader works in display space (the texture is NoColorSpace, see loadTexture),
  // so the void colour must not be colour-managed either: `new Color(0x0e1116)` would be
  // converted to linear (1,1,2 on screen) and the print's (14,17,22) would not match.
  uBackground: { value: new THREE.Color().setRGB(14 / 255, 17 / 255, 22 / 255, THREE.NoColorSpace) },
  uVignette: { value: 0.25 },
  uPanniniD: { value: 1.0 },
};

const material = new THREE.ShaderMaterial({
  vertexShader: VERT,
  fragmentShader: FRAG,
  uniforms,
  glslVersion: THREE.GLSL3,
  depthTest: false,
  depthWrite: false,
});
scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material));

function resize() {
  const w = canvas.clientWidth || innerWidth;
  const h = canvas.clientHeight || innerHeight;
  renderer.setSize(w, h, false);
  uniforms.uAspect.value = w / h;
}
addEventListener('resize', resize);

// ---------------------------------------------------------------------------
// Camera orientation
// ---------------------------------------------------------------------------

// Same convention as the stitcher: X east, Y up, Z north; yaw 0 looks north and
// increases clockwise; pitch 0 is level and -90 is straight down.
function buildRotation(yaw, pitch, roll) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const fwd = new THREE.Vector3(sy * cp, sp, cy * cp);
  const right = new THREE.Vector3(cy, 0, -sy);
  const up = new THREE.Vector3().crossVectors(fwd, right).normalize();
  if (roll) {
    const q = new THREE.Quaternion().setFromAxisAngle(fwd, roll);
    right.applyQuaternion(q);
    up.applyQuaternion(q);
  }
  // shader expects view-space (x right, y up, z forward) -> world
  return new THREE.Matrix3().set(
    right.x, up.x, fwd.x,
    right.y, up.y, fwd.y,
    right.z, up.z, fwd.z,
  );
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

const loader = new THREE.TextureLoader();

function loadTexture(url) {
  return new Promise((resolve, reject) => {
    loader.load(url, (tex) => {
      // NoColorSpace, deliberately. With SRGBColorSpace the GPU decodes the JPEG to
      // linear light on sampling, but a ShaderMaterial does not re-encode on output, so
      // the screen showed roughly pow(x, 2.2) of the file -- every panorama rendered
      // darker than it is. Passing the bytes through untouched matches the file, matches
      // the prints (which sample the raw bytes), and is what the user composed against.
      tex.colorSpace = THREE.NoColorSpace;
      // The shader maps v=0 to latitude +90, i.e. the FIRST row of the equirect.
      // three.js flips textures vertically by default, which would put the sky at
      // the bottom and turn every panorama upside down.
      tex.flipY = false;
      tex.wrapS = THREE.RepeatWrapping;      // adjusted per panorama in applyTexture
      tex.wrapT = THREE.ClampToEdgeWrapping;
      tex.minFilter = THREE.LinearMipmapLinearFilter;
      tex.magFilter = THREE.LinearFilter;
      tex.generateMipmaps = true;
      const max = renderer.capabilities.getMaxAnisotropy();
      tex.anisotropy = Math.min(8, max);
      resolve(tex);
    }, undefined, reject);
  });
}

async function openEntry(entry) {
  state.entry = entry;
  setStatus(`Loading ${entry.name}…`);

  // show the low-resolution preview immediately, then upgrade -- an 8k equirect
  // takes a moment to decode and a blank screen in the meantime feels broken
  if (entry.preview) {
    try {
      const tex = await loadTexture(entry.preview);
      applyTexture(tex, entry);
    } catch (_) { /* preview is optional */ }
  }

  const full = entry.equirect || entry.preview;
  if (full && full !== entry.preview) {
    try {
      const tex = await loadTexture(full);
      applyTexture(tex, entry);
    } catch (err) {
      setStatus(`Could not load ${entry.name}`);
      return;
    }
  }

  const cov = entry.coverage || {};
  const lon0 = (cov.lon_min ?? -180) * DEG;
  const lon1 = (cov.lon_max ?? 180) * DEG;
  const lat0 = (cov.lat_min ?? -90) * DEG;
  const lat1 = (cov.lat_max ?? 90) * DEG;
  uniforms.uLonRange.value.set(lon0, lon1);
  uniforms.uLatRange.value.set(lat0, lat1);

  // Opening a partial panorama while a sphere-only mode is selected would leave the
  // view on a disabled mode; fall back to the one that always works.
  if (modeByKey(state.modeKey).needsSphere && !isSphere(entry)) {
    state.modeKey = 'immersive';
  }
  applyMode(state.modeKey, true);
  renderInfo(entry);
  setStatus('');
}

function applyTexture(tex, entry) {
  if (uniforms.uPano.value && uniforms.uPano.value !== tex) uniforms.uPano.value.dispose?.();
  // A full sphere wraps at +/-180; a partial sweep must clamp, or the far edge of its
  // coverage bleeds into the near edge. Same rule as the print renderer's `span >= 2pi`.
  const span = entry?.coverage?.lon_span ?? 360;
  tex.wrapS = span >= 359 ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  uniforms.uPano.value = tex;
}

// ---------------------------------------------------------------------------
// Modes
// ---------------------------------------------------------------------------

function isSphere(entry) {
  if (!entry) return false;
  const c = entry.coverage || {};
  return (c.lon_span ?? 360) >= 350 && (c.lat_span ?? 180) >= 150;
}

function applyMode(key, resetView) {
  const m = modeByKey(key);
  state.modeKey = key;
  uniforms.uMode.value = m.id;

  if (resetView) {
    state.targetFov = m.defaultFov;
    state.fov = m.defaultFov;
    state.morph = 0;
    if (m.pitch !== undefined) {
      state.targetPitch = m.pitch * DEG;
      state.pitch = state.targetPitch;
    } else {
      state.targetPitch = 0;
      state.pitch = 0;
    }
  }

  for (const btn of document.querySelectorAll('[data-mode]')) {
    btn.classList.toggle('on', btn.dataset.mode === key);
    const md = modeByKey(btn.dataset.mode);
    const disabled = md.needsSphere && !isSphere(state.entry);
    btn.disabled = disabled;
    btn.title = disabled
      ? `${md.blurb} — needs a full 360° sphere; this panorama is partial.`
      : md.blurb;
  }

  document.getElementById('modeBlurb').textContent = m.blurb;
  const morphRow = document.getElementById('morphRow');
  morphRow.style.display = m.morph ? '' : 'none';
  const globeRow = document.getElementById('globeRow');
  globeRow.style.display = (m.key === 'globe') ? '' : 'none';
}

// ---------------------------------------------------------------------------
// Interaction
// ---------------------------------------------------------------------------

canvas.addEventListener('pointerdown', (e) => {
  state.dragging = true;
  state.lastX = e.clientX; state.lastY = e.clientY;
  canvas.setPointerCapture(e.pointerId);
  state.autoSpin = false;
  syncSpinButton();
});

canvas.addEventListener('pointermove', (e) => {
  if (!state.dragging) return;
  const dx = e.clientX - state.lastX;
  const dy = e.clientY - state.lastY;
  state.lastX = e.clientX; state.lastY = e.clientY;
  // scale by field of view so the drag feels the same whether zoomed in or out
  const k = (state.fov * DEG) / Math.max(canvas.clientHeight, 1) * 1.15;
  state.velYaw = -dx * k;
  state.velPitch = -dy * k;
});

function endDrag() { state.dragging = false; }
canvas.addEventListener('pointerup', endDrag);
canvas.addEventListener('pointercancel', endDrag);

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const m = modeByKey(state.modeKey);
  if (m.key === 'globe') {
    state.globeDist = clamp(state.globeDist * (1 + Math.sign(e.deltaY) * 0.08), 1.25, 9);
  } else {
    const [lo, hi] = m.fovRange;
    if (lo === hi) return;
    state.targetFov = clamp(state.targetFov * (1 + Math.sign(e.deltaY) * 0.07), lo, hi);
  }
}, { passive: false });

addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  // Ctrl+P is the print shortcut; without this guard 'p' would also snap the stage to
  // Little Planet an instant before the panel captured "this view".
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const idx = MODES.findIndex((m) => m.key === state.modeKey);
  switch (e.key) {
    case ' ': state.autoSpin = !state.autoSpin; syncSpinButton(); e.preventDefault(); break;
    case 'ArrowRight': if (e.shiftKey) { applyMode(MODES[(idx + 1) % MODES.length].key, true); } break;
    case 'ArrowLeft': if (e.shiftKey) { applyMode(MODES[(idx - 1 + MODES.length) % MODES.length].key, true); } break;
    case 'g': applyMode('globe', true); break;
    case 'i': applyMode('immersive', true); break;
    case 'p': applyMode('planet', true); break;
    case 't': applyMode('tunnel', true); break;
    case 'f': toggleFullscreen(); break;
    case 'h': document.body.classList.toggle('chrome-hidden'); break;
    case 'r': state.targetYaw = 0; state.targetPitch = 0; break;
  }
});

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.();
}

// ---------------------------------------------------------------------------
// Frame loop
// ---------------------------------------------------------------------------

let last = performance.now();
function frame(now) {
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;

  if (state.autoSpin) state.targetYaw += state.spinSpeed * dt * Math.PI;

  if (state.dragging) {
    state.targetYaw += state.velYaw;
    state.targetPitch += state.velPitch;
    state.velYaw = 0; state.velPitch = 0;
  } else {
    // a little inertia so a flick keeps gliding instead of stopping dead
    if (Math.abs(state.velYaw) > 1e-5 || Math.abs(state.velPitch) > 1e-5) {
      state.targetYaw += state.velYaw;
      state.targetPitch += state.velPitch;
      state.velYaw *= 0.92; state.velPitch *= 0.92;
    }
  }

  const m = modeByKey(state.modeKey);
  // Looking past straight up or down would roll the horizon over; clamp everywhere
  // except the stereographic modes, which are meant to sit exactly at a pole.
  if (m.id !== 1) {
    state.targetPitch = clamp(state.targetPitch, -89.5 * DEG, 89.5 * DEG);
  } else {
    state.targetPitch = clamp(state.targetPitch, -100 * DEG, 100 * DEG);
  }

  const s = 1 - Math.pow(0.0015, dt);
  state.yaw += (state.targetYaw - state.yaw) * s;
  state.pitch += (state.targetPitch - state.pitch) * s;
  state.fov += (state.targetFov - state.fov) * s;

  uniforms.uCamRot.value = buildRotation(state.yaw, state.pitch, state.roll);
  uniforms.uFov.value = state.fov * DEG;
  uniforms.uMorph.value = state.morph;
  uniforms.uExposure.value = state.exposure;
  uniforms.uGlobeDist.value = state.globeDist;

  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------------------
// UI
// ---------------------------------------------------------------------------

function setStatus(msg) {
  const el = document.getElementById('status');
  el.textContent = msg || '';
  el.style.display = msg ? '' : 'none';
}

function syncSpinButton() {
  const b = document.getElementById('spinBtn');
  if (b) b.classList.toggle('on', state.autoSpin);
}

function renderInfo(entry) {
  const bits = [];
  if (entry.mode_label) bits.push(entry.mode_label);
  if (entry.tile_count) bits.push(`${entry.tile_count} tiles`);
  if (entry.captured) bits.push(entry.captured.slice(0, 10));
  if (entry.coverage?.lon_span) {
    bits.push(`${Math.round(entry.coverage.lon_span)}° × ${Math.round(entry.coverage.lat_span)}°`);
  }
  if (entry.altitude_rel != null) bits.push(`${Math.round(entry.altitude_rel)} m AGL`);
  const ref = entry.refinement;
  if (ref && ref.applied) {
    bits.push(`de-ghosted ${ref.max_correction_deg.toFixed(1)}°`);
  }
  document.getElementById('meta').textContent = bits.join('  ·  ');
  const metaEl = document.getElementById('meta');
  metaEl.title = (ref && ref.reason)
    ? `Angle refinement: ${ref.reason}`
    : 'Angle refinement was not applied to this panorama.';
  document.getElementById('title').textContent = entry.name || entry.id;

  const q = document.getElementById('quality');
  if (entry.quality != null) {
    q.textContent = `quality ${Math.round(entry.quality * 100)}`;
    q.className = 'quality ' + (entry.quality > 0.75 ? 'good' : entry.quality > 0.5 ? 'ok' : 'poor');
    q.style.display = '';
  } else {
    q.style.display = 'none';
  }

  if (entry.lat != null && entry.lon != null) {
    const a = document.getElementById('mapLink');
    a.href = `https://www.openstreetmap.org/?mlat=${entry.lat}&mlon=${entry.lon}#map=14/${entry.lat}/${entry.lon}`;
    a.style.display = '';
  } else {
    document.getElementById('mapLink').style.display = 'none';
  }

  renderNearby(entry);
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
const compassOf = (deg) => COMPASS[Math.round(((deg % 360) + 360) % 360 / 45) % 8];

// Panoramas shot near each other are viewpoints on the same place, so offer them as
// somewhere to walk to rather than making the user go back to the gallery and guess.
function renderNearby(entry) {
  const box = document.getElementById('nearby');
  const list = entry.nearby || [];
  if (!list.length) { box.style.display = 'none'; return; }
  box.style.display = '';
  box.innerHTML = '<span class="nearby-label">also here</span>';
  const byId = new Map((state.library?.panoramas || []).map((p) => [p.id, p]));
  for (const n of list.slice(0, 5)) {
    const target = byId.get(n.id);
    if (!target || target.status !== 'ok') continue;
    const b = document.createElement('button');
    const dist = n.distance_m < 1000
      ? `${Math.round(n.distance_m)} m`
      : `${(n.distance_m / 1000).toFixed(1)} km`;
    b.textContent = `${compassOf(n.bearing)} · ${dist}`;
    b.title = `${n.name}${n.captured ? ' — ' + n.captured.slice(0, 10) : ''}`;
    b.onclick = () => openEntry(target);
    box.appendChild(b);
  }
}

function buildModeBar() {
  const bar = document.getElementById('modes');
  bar.innerHTML = '';
  for (const m of MODES) {
    const b = document.createElement('button');
    b.textContent = m.name;
    b.dataset.mode = m.key;
    b.title = m.blurb;
    b.onclick = () => applyMode(m.key, true);
    bar.appendChild(b);
  }
}

// Group by where the panoramas were actually taken (derived from their GPS at build
// time), falling back to the backup folder when the library predates that or a set has
// no position. The folders are unreliable: one holds five Indian states and a corner of
// California. The user can flip to folder grouping with the toggle.
let groupMode = 'location';

function buildGallery(library) {
  const g = document.getElementById('gallery');
  g.innerHTML = '';

  const hasLocation = library.panoramas.some((e) => e.location_trip);
  const toggle = document.createElement('div');
  toggle.className = 'group-toggle';
  for (const [mode, label] of [['location', 'By place'], ['folder', 'By folder']]) {
    const b = document.createElement('button');
    b.textContent = label;
    b.classList.toggle('on', groupMode === mode);
    b.disabled = mode === 'location' && !hasLocation;
    b.onclick = () => { groupMode = mode; buildGallery(library); };
    toggle.appendChild(b);
  }
  g.appendChild(toggle);

  const keyOf = (e) => (groupMode === 'location' && e.location_trip)
    ? e.location_trip
    : (e.source_folder || e.trip || 'Other');

  const groups = new Map();
  for (const e of library.panoramas) {
    const k = keyOf(e);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(e);
  }

  // in place mode, order trips chronologically; in folder mode keep folder order
  const ordered = [...groups.entries()];
  if (groupMode === 'location') {
    const firstDate = (items) => items.map((e) => e.captured || '').filter(Boolean).sort()[0] || '';
    ordered.sort((a, b) => firstDate(a[1]).localeCompare(firstDate(b[1])));
  }

  for (const [trip, items] of ordered) {
    const h = document.createElement('div');
    h.className = 'trip';
    h.textContent = `${trip} (${items.length})`;
    if (groupMode === 'location') {
      const towns = [...new Set(items.map((e) => e.location?.town).filter(Boolean))].slice(0, 3);
      const misfiled = items.filter((e) => e.misfiled).length;
      const sub = document.createElement('div');
      sub.className = 'trip-sub';
      sub.textContent = towns.join(' · ') + (misfiled ? `  ·  ${misfiled} filed elsewhere` : '');
      g.appendChild(h);
      g.appendChild(sub);
    } else {
      g.appendChild(h);
    }
    const grid = document.createElement('div');
    grid.className = 'grid';
    for (const e of items) {
      const card = document.createElement('button');
      card.className = 'card';
      card.title = `${e.name} — ${e.mode_label || ''}`;
      if (e.thumb) {
        const im = document.createElement('img');
        im.src = e.thumb; im.loading = 'lazy'; im.alt = e.name;
        card.appendChild(im);
      }
      const cap = document.createElement('span');
      cap.textContent = e.name;
      card.appendChild(cap);
      if (e.status && e.status !== 'ok') card.classList.add('bad');
      card.onclick = () => { openEntry(e); document.body.classList.remove('browsing'); };
      grid.appendChild(card);
    }
    g.appendChild(grid);
  }
}

function wireControls() {
  document.getElementById('morph').oninput = (e) => { state.morph = +e.target.value; };
  document.getElementById('exposure').oninput = (e) => { state.exposure = +e.target.value; };
  document.getElementById('globeDist').oninput = (e) => { state.globeDist = +e.target.value; };
  document.getElementById('spinBtn').onclick = () => { state.autoSpin = !state.autoSpin; syncSpinButton(); };
  document.getElementById('fsBtn').onclick = toggleFullscreen;
  document.getElementById('browseBtn').onclick = () => document.body.classList.toggle('browsing');
}

// ---------------------------------------------------------------------------
// Print panel
// ---------------------------------------------------------------------------
//
// Only available when the page is served by `panolib view`, which injects a
// per-session token into the <meta name="print-token"> tag. A static copy of the page
// has the placeholder instead, so the button simply stays hidden there.

const VIEW_PRINTABLE = new Set(['immersive', 'planet', 'tunnel', 'fisheye', 'pannini']);
const PRINT_SIZES = ['a4', 'a3', 'a2', '8x10', '11x14', '12x18', '16x20', '16x24',
                     '20x30', '24x36', 'pano-2to1', 'pano-3to1', 'square'];

function printToken() {
  const t = document.querySelector('meta[name="print-token"]')?.content || '';
  return (t && !t.startsWith('__')) ? t : null;
}

function currentView() {
  const TAU = 2 * Math.PI;
  return {
    mode: state.modeKey,
    // yaw grows without bound during auto-spin; send the bearing, not the turn count
    yaw: ((state.yaw % TAU) + TAU) % TAU,
    pitch: state.pitch, roll: state.roll,
    fov: state.fov, morph: state.morph,
    exposure: state.exposure,          // "print what I see" includes the slider
    aspect: (canvas.clientWidth || 3) / (canvas.clientHeight || 2),
  };
}

async function postPrint(body) {
  const res = await fetch('/api/print', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Print-Token': printToken() || '' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data.job;
}

const JOB_WAIT_LIMIT_MS = 30 * 60 * 1000;

async function waitForJob(job, onProgress) {
  const started = Date.now();
  for (;;) {
    const res = await fetch(`/api/jobs/${job}`, { cache: 'no-store' });
    const j = await res.json().catch(() => ({}));
    // A 404 means the job record is gone (server restarted, or evicted); without this
    // check the loop would poll forever and leave the panel's buttons dead.
    if (!res.ok || !j.state) throw new Error(j.error || `the print job was lost (HTTP ${res.status}); print again`);
    if (j.state === 'done') return j.result;
    if (j.state === 'error') throw new Error(j.error || 'print failed');
    onProgress?.(j.progress || 0);
    if (Date.now() - started > JOB_WAIT_LIMIT_MS) throw new Error('gave up waiting for the print');
    await new Promise((r) => setTimeout(r, 400));
  }
}

function syncPrintPanel() {
  const viewOk = VIEW_PRINTABLE.has(state.modeKey);
  const viewRadio = document.querySelector('input[name="psrc"][value="view"]');
  const eqRadio = document.querySelector('input[name="psrc"][value="equirect"]');
  viewRadio.disabled = !viewOk;
  viewRadio.parentElement.title = viewOk ? ''
    : 'This mode is a diagnostic layout; switch to Immersive, Little Planet, Tunnel, Fisheye or Pannini to print a view.';
  if (!viewOk) eqRadio.checked = true;
  const title = document.getElementById('pTitle');
  if (!title.value && state.entry?.location) {
    title.placeholder = `blank = derived (${state.entry.location.town}, ${state.entry.location.region})`;
  }
}

function wirePrintPanel() {
  const btn = document.getElementById('printBtn');
  if (!printToken()) return;              // static page: no API behind it
  btn.style.display = '';

  const sizeSel = document.getElementById('pSize');
  for (const s of PRINT_SIZES) {
    const o = document.createElement('option');
    o.value = s; o.textContent = s;
    if (s === 'a3') o.selected = true;
    sizeSel.appendChild(o);
  }

  const open = () => { document.body.classList.add('printing'); syncPrintPanel(); };
  const close = () => document.body.classList.remove('printing');
  btn.onclick = open;
  document.getElementById('printClose').onclick = close;
  addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    if (e.key === 'p' && e.ctrlKey) { e.preventDefault(); open(); }
  });

  const stateEl = document.getElementById('pState');
  const warnEl = document.getElementById('pWarn');
  const proofImg = document.getElementById('pProofImg');
  const resultEl = document.getElementById('pResult');
  let busy = false;

  async function go(proof) {
    if (busy || !state.entry) return;
    busy = true;
    warnEl.style.display = 'none';
    resultEl.style.display = 'none';
    stateEl.textContent = proof ? 'rendering proof…' : 'queued…';
    try {
      const body = {
        id: state.entry.id,
        source: document.querySelector('input[name="psrc"]:checked').value,
        view: currentView(),
        size: sizeSel.value,
        dpi: +document.getElementById('pDpi').value,
        style: document.getElementById('pStyle').value,
        fit: document.getElementById('pFit').value,
        title: document.getElementById('pTitle').value.trim() || null,
        proof,
      };
      const job = await postPrint(body);
      const result = await waitForJob(job, (p) => {
        stateEl.textContent = `${proof ? 'proof' : 'rendering'} ${Math.round(p * 100)}%`;
      });
      stateEl.textContent = '';
      if (result.warning) { warnEl.textContent = result.warning; warnEl.style.display = ''; }
      if (proof) {
        proofImg.src = result.url + '?t=' + Date.now();
        proofImg.style.display = '';
      } else {
        proofImg.style.display = 'none';
        // Built with DOM nodes, not innerHTML: the title is user text echoed back by
        // the server, and text must stay text.
        resultEl.replaceChildren();
        const line = (...parts) => {
          const div = document.createElement('div');
          for (const p of parts) div.append(p);
          resultEl.appendChild(div);
        };
        const strong = document.createElement('strong'); strong.textContent = 'Saved';
        line(strong, ` ${result.width}×${result.height} px · ${result.paper}`);
        line(`${result.effective_dpi} dpi effective`);
        if (result.title) line(`title: ${result.title}`);
        const a = document.createElement('a');
        a.href = result.url; a.target = '_blank'; a.rel = 'noopener'; a.textContent = 'open the print';
        if (result.sidecar) {
          const b = document.createElement('a');
          b.href = result.sidecar; b.target = '_blank'; b.rel = 'noopener'; b.textContent = 'record';
          line(a, ' · ', b);
        } else {
          line(a);
        }
        const path = document.createElement('span');
        path.style.opacity = '.7'; path.textContent = result.path;
        line(path);
        resultEl.style.display = '';
      }
    } catch (err) {
      stateEl.textContent = '';
      warnEl.textContent = String(err.message || err);
      warnEl.style.display = '';
    } finally {
      busy = false;
    }
  }
  document.getElementById('pProof').onclick = () => go(true);
  document.getElementById('pPrint').onclick = () => go(false);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  resize();
  buildModeBar();
  wireControls();
  wirePrintPanel();
  requestAnimationFrame(frame);

  let lib;
  try {
    const res = await fetch('./library.json', { cache: 'no-store' });
    if (!res.ok) throw new Error(String(res.status));
    lib = await res.json();
  } catch (err) {
    setStatus('No library.json yet — run the stitcher first:  python -m panolib build <folder>');
    return;
  }
  state.library = lib;
  if (!lib.panoramas || !lib.panoramas.length) {
    setStatus('The library is empty — no panorama sets were found.');
    return;
  }
  buildGallery(lib);
  await openEntry(lib.panoramas[0]);
}

boot();
