// SoukSaaS agent network (ES module, inlined by dashboard/three_scene.py).
// three.js r160 is resolved through the importmap in the host page and loaded with dynamic import()
// so that a blocked CDN or missing WebGL falls back to the static SVG map instead of a blank frame.
// Colours, bloom and blending come from DATA.theme (dark or light mode, see dashboard/palette.py).
const DATA = JSON.parse(document.getElementById("scene-data").textContent);
const T = DATA.theme;
const hex = (s) => parseInt(s.replace("#", ""), 16);
const ACCENT = hex(T.accent);
const ALARM = hex(T.alarm);
const BG = hex(T.bg);
const stage = document.getElementById("stage");
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let THREE = null;
const disposables = [];
const track = (o) => { disposables.push(o); return o; };

function showFallback(reason) {
  document.body.classList.add("nogl");
  const note = document.getElementById("fallback-note");
  if (note) note.textContent = reason;
}

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

const maxAct = Math.max(1, ...Object.values(DATA.activity || {}));
const level = (k) => ((DATA.activity || {})[k] || 0) / maxAct;
const flaggedRecent = (k) => ((DATA.recent_flagged || {})[k] || 0);
const blending = () => (T.additive ? THREE.AdditiveBlending : THREE.NormalBlending);

function dotTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.35, "rgba(255,255,255,0.55)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return track(new THREE.CanvasTexture(c));
}

function circleLoop(radius, y, opacity) {
  const pts = [];
  for (let i = 0; i < 128; i++) {
    const a = (i / 128) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * radius, y, Math.sin(a) * radius));
  }
  const geo = track(new THREE.BufferGeometry().setFromPoints(pts));
  const mat = track(new THREE.LineBasicMaterial({ color: hex(T.ring), transparent: true, opacity }));
  return new THREE.LineLoop(geo, mat);
}

function buildBackdrop(scene) {
  [3.5, 6.5, 9.5].forEach((r, i) => scene.add(circleLoop(r, -4.4, Math.max(0.15, T.ring_opacity - i * 0.14))));
  const n = 260;
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const r = 16 + Math.random() * 18;
    const th = Math.random() * Math.PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
    pos[i * 3 + 1] = r * Math.cos(ph);
    pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
  }
  const geo = track(new THREE.BufferGeometry());
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const mat = track(new THREE.PointsMaterial({
    color: hex(T.star), size: 0.07, transparent: true, opacity: T.star_opacity }));
  scene.add(new THREE.Points(geo, mat));
}

function buildNodes(group, CSS2DObject) {
  const out = {};
  for (const n of DATA.nodes) {
    const hub = n.key === "orchestrator";
    const r = hub ? 0.36 : 0.2 + 0.12 * level(n.key);
    const holder = new THREE.Group();
    holder.position.set(n.pos[0], n.pos[1], n.pos[2]);

    const coreMat = track(new THREE.MeshBasicMaterial({ color: new THREE.Color(ACCENT).multiplyScalar(T.core_boost) }));
    const core = new THREE.Mesh(track(new THREE.SphereGeometry(r, 24, 16)), coreMat);
    const shellMat = track(new THREE.LineBasicMaterial({ color: ACCENT, transparent: true, opacity: T.shell_opacity }));
    const shell = new THREE.LineSegments(
      track(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(r * 2.1, 1))), shellMat);
    const ringMat = track(new THREE.MeshBasicMaterial({
      color: ALARM, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    const ring = new THREE.Mesh(track(new THREE.RingGeometry(r * 1.5, r * 1.62, 64)), ringMat);
    const hitMat = track(new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
    const hit = new THREE.Mesh(track(new THREE.SphereGeometry(r * 2.4, 12, 8)), hitMat);
    hit.userData.key = n.key;
    holder.add(core, shell, ring, hit);

    const div = document.createElement("div");
    const flagged = flaggedRecent(n.key) > 0;
    div.className = "lbl" + (flagged ? " bad" : "");
    const count = (DATA.activity || {})[n.key] || 0;
    div.innerHTML = "<b></b><span></span>";
    div.firstChild.textContent = n.label;
    div.lastChild.textContent = count + " ev" + (flagged ? " / " + flaggedRecent(n.key) + " flagged" : "");
    const label = new CSS2DObject(div);
    label.position.set(0, r * 2.4 + 0.22, 0);
    holder.add(label);

    group.add(holder);
    out[n.key] = { holder, core, shell, ring, hit, div, coreMat, shellMat, ringMat, r, flagged, hover: 0,
                   phase: Math.random() * 6.28, rate: Math.min(2.2, 0.8 + flaggedRecent(n.key) * 0.25),
                   nextBurst: 1.2 + Math.random() * 2 };
  }
  return out;
}

function buildLinks(group, nodes) {
  const up = new THREE.Vector3(0, 1, 0);
  return DATA.links.map(([a, b]) => {
    const A = nodes[a].holder.position;
    const B = nodes[b].holder.position;
    const d = B.clone().sub(A);
    let side = new THREE.Vector3().crossVectors(d, up);
    if (side.lengthSq() < 1e-6) side = new THREE.Vector3(1, 0, 0);
    const mid = A.clone().add(B).multiplyScalar(0.5)
      .add(side.normalize().multiplyScalar(d.length() * 0.12)).add(new THREE.Vector3(0, 0.35, 0));
    const curve = new THREE.QuadraticBezierCurve3(A.clone(), mid, B.clone());
    const geo = track(new THREE.BufferGeometry().setFromPoints(curve.getPoints(40)));
    const mat = track(new THREE.LineBasicMaterial({ color: hex(T.link), transparent: true, opacity: T.link_opacity }));
    group.add(new THREE.Line(geo, mat));
    return { a, b, curve, mat, hot: 0 };
  });
}

function buildParticles(group, links) {
  const parts = [];
  links.forEach((l, i) => {
    const rate = 0.12 + 0.88 * ((level(l.a) + level(l.b)) / 2);
    const hot = flaggedRecent(l.a) > 0 || flaggedRecent(l.b) > 0;
    const count = Math.round(3 + rate * 13);
    for (let j = 0; j < count; j++) {
      parts.push({ li: i, t: Math.random(), v: (0.05 + rate * 0.17) * (0.8 + Math.random() * 0.4),
                   alarm: hot && Math.random() < 0.35 });
    }
  });
  const pos = new Float32Array(parts.length * 3);
  const col = new Float32Array(parts.length * 3);
  const boost = T.additive ? 1.5 : 1;
  const a = new THREE.Color(ACCENT).multiplyScalar(boost);
  const b = new THREE.Color(ALARM).multiplyScalar(boost);
  parts.forEach((p, i) => (p.alarm ? b : a).toArray(col, i * 3));
  const geo = track(new THREE.BufferGeometry());
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const mat = track(new THREE.PointsMaterial({
    size: T.additive ? 0.2 : 0.24, map: dotTexture(), vertexColors: true, transparent: true, depthWrite: false,
    blending: blending() }));
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false;
  group.add(points);
  const tmp = new THREE.Vector3();
  const step = (dt) => {
    parts.forEach((p, i) => {
      p.t = (p.t + p.v * dt) % 1;
      links[p.li].curve.getPoint(p.t, tmp);
      pos[i * 3] = tmp.x; pos[i * 3 + 1] = tmp.y; pos[i * 3 + 2] = tmp.z;
    });
    geo.attributes.position.needsUpdate = true;
  };
  step(0);
  return step;
}

// Short alarm-coloured bursts that radiate from nodes with recent block/escalate events.
function buildBursts(group, nodes) {
  const N = 220;
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const life = new Float32Array(N);
  const geo = track(new THREE.BufferGeometry());
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const mat = track(new THREE.PointsMaterial({
    size: 0.17, map: dotTexture(), vertexColors: true, transparent: true, depthWrite: false, blending: blending() }));
  const points = new THREE.Points(geo, mat);
  points.frustumCulled = false;
  group.add(points);
  const bg = new THREE.Color(BG);
  const hot = new THREE.Color(ALARM).multiplyScalar(T.additive ? 1.6 : 1);
  const c = new THREE.Color();
  let cursor = 0;
  const emit = (p, count) => {
    for (let k = 0; k < count; k++) {
      const i = cursor++ % N;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      const sp = 0.9 + Math.random() * 1.3;
      pos.set([p.x, p.y, p.z], i * 3);
      vel.set([Math.sin(ph) * Math.cos(th) * sp, Math.cos(ph) * sp, Math.sin(ph) * Math.sin(th) * sp], i * 3);
      life[i] = 1;
    }
  };
  const flagged = Object.values(nodes).filter((n) => n.flagged);
  return (dt) => {
    for (const n of flagged) {
      n.nextBurst -= dt;
      if (n.nextBurst <= 0) {
        emit(n.holder.position, 14);
        n.nextBurst = (2.4 + Math.random() * 2.4) / n.rate;
      }
    }
    for (let i = 0; i < N; i++) {
      if (life[i] <= 0) { col.set([bg.r, bg.g, bg.b], i * 3); continue; }
      life[i] = Math.max(0, life[i] - dt * 0.75);
      for (let a = 0; a < 3; a++) { pos[i * 3 + a] += vel[i * 3 + a] * dt; vel[i * 3 + a] *= 1 - dt * 1.6; }
      c.copy(bg).lerp(hot, life[i] * life[i]);
      col.set([c.r, c.g, c.b], i * 3);
    }
    geo.attributes.position.needsUpdate = true;
    geo.attributes.color.needsUpdate = true;
  };
}

function pulseNodes(nodes, links, t, camera) {
  const c = new THREE.Color();
  const alarm = new THREE.Color(ALARM);
  const base = new THREE.Color(ACCENT).multiplyScalar(T.core_boost);
  const gl = T.additive ? [1.15, 0.45] : [0.95, 0.12];
  for (const n of Object.values(nodes)) {
    n.shell.rotation.y = t * 0.15 + n.phase;
    n.shell.rotation.x = t * 0.08;
    const h = n.hover;
    const s = 1 + 0.35 * h;
    n.core.scale.setScalar(s);
    n.shell.scale.setScalar(1 + 0.12 * h);
    n.shellMat.opacity = Math.min(1, T.shell_opacity + 0.5 * h);
    if (!n.flagged) { n.coreMat.color.copy(base).multiplyScalar(1 + 0.25 * h); continue; }
    const k = (t * 0.55 * n.rate + n.phase) % 1;
    n.ring.quaternion.copy(camera.quaternion);
    n.ring.scale.setScalar(1 + k * 2.4);
    n.ringMat.opacity = (1 - k) * 0.8;
    const glow = gl[0] + gl[1] * Math.sin(t * 3 * n.rate + n.phase);
    n.coreMat.color.copy(c.copy(alarm).multiplyScalar(glow));
    n.shellMat.color.set(ALARM);
  }
  const lit = new THREE.Color(hex(T.link_hot));
  const dim = new THREE.Color(hex(T.link));
  for (const l of links) {
    const target = nodes[l.a].hover > 0.5 || nodes[l.b].hover > 0.5 ? 1 : 0;
    l.hot += (target - l.hot) * 0.2;
    l.mat.color.copy(dim).lerp(lit, l.hot);
    l.mat.opacity = T.link_opacity + (1 - T.link_opacity) * l.hot;
  }
}

async function start() {
  const base = "three/addons/";
  const [three, ec, rp, ub, op, oc, cr] = await Promise.all([
    import("three"),
    import(base + "postprocessing/EffectComposer.js"),
    import(base + "postprocessing/RenderPass.js"),
    import(base + "postprocessing/UnrealBloomPass.js"),
    import(base + "postprocessing/OutputPass.js"),
    import(base + "controls/OrbitControls.js"),
    import(base + "renderers/CSS2DRenderer.js"),
  ]);
  THREE = three;
  const size = () => [Math.max(stage.clientWidth, 10), Math.max(stage.clientHeight, 10)];
  let [w, h] = size();

  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w, h);
  renderer.setClearColor(BG, 1);
  stage.appendChild(renderer.domElement);
  const labels = new cr.CSS2DRenderer();
  labels.setSize(w, h);
  labels.domElement.className = "labels";
  stage.appendChild(labels.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.FogExp2(BG, T.fog);
  const camera = new THREE.PerspectiveCamera(42, w / h, 0.1, 120);
  camera.position.set(7.6, 3.8, 10.2);
  camera.zoom = reduced ? 1 : 0.55;
  camera.updateProjectionMatrix();
  const controls = new oc.OrbitControls(camera, renderer.domElement);
  Object.assign(controls, { enableZoom: false, enablePan: false, enableDamping: true, dampingFactor: 0.06,
    autoRotate: !reduced, autoRotateSpeed: 0.45, minPolarAngle: Math.PI * 0.28, maxPolarAngle: Math.PI * 0.68 });

  const composer = new ec.EffectComposer(renderer);
  composer.setSize(w, h);
  composer.addPass(new rp.RenderPass(scene, camera));
  if (T.bloom) {
    composer.addPass(new ub.UnrealBloomPass(new THREE.Vector2(w, h), T.bloom.strength, T.bloom.radius, T.bloom.threshold));
  }
  composer.addPass(new op.OutputPass());

  buildBackdrop(scene);
  const group = new THREE.Group();
  group.position.x = -DATA.nodes.reduce((s, n) => s + n.pos[0], 0) / DATA.nodes.length;
  scene.add(group);
  const nodes = buildNodes(group, cr.CSS2DObject);
  const links = buildLinks(group, nodes);
  const stepParticles = buildParticles(group, links);
  const stepBursts = reduced ? () => {} : buildBursts(group, nodes);

  // hover highlight: raycast against the invisible hit spheres
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const hits = Object.values(nodes).map((n) => n.hit);
  let hoverKey = null;
  const setHover = (key) => {
    if (key === hoverKey) return;
    hoverKey = key;
    for (const [k, n] of Object.entries(nodes)) n.div.classList.toggle("hot", k === key);
    controls.autoRotate = !reduced && !key;
    renderer.domElement.style.cursor = key ? "pointer" : "grab";
    if (reduced) { for (const [k, n] of Object.entries(nodes)) n.hover = k === key ? 1 : 0; draw(); }
  };
  renderer.domElement.addEventListener("pointermove", (e) => {
    const r = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(hits, false)[0];
    setHover(hit ? hit.object.userData.key : null);
  });
  renderer.domElement.addEventListener("pointerleave", () => setHover(null));

  const clock = new THREE.Clock();
  const ease = (t) => 1 - Math.pow(1 - t, 3);
  let raf = 0;
  let running = false;
  const draw = () => {
    controls.update();
    pulseNodes(nodes, links, clock.elapsedTime, camera);
    composer.render();
    labels.render(scene, camera);
  };
  const frame = () => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(clock.getDelta(), 0.05);
    const t = clock.elapsedTime;
    if (t < 2.6) {   // camera push-in on load
      camera.zoom = 0.55 + 0.45 * ease(Math.min(1, t / 2.4));
      camera.updateProjectionMatrix();
    }
    for (const [k, n] of Object.entries(nodes)) n.hover += ((k === hoverKey ? 1 : 0) - n.hover) * 0.18;
    stepParticles(dt);
    stepBursts(dt);
    draw();
  };
  const setRunning = (on) => {
    if (on && !running && !reduced) { running = true; clock.getDelta(); frame(); }
    if (!on && running) { running = false; cancelAnimationFrame(raf); }
  };
  if (reduced) controls.addEventListener("change", draw);
  draw();

  const onResize = () => {
    [w, h] = size();
    renderer.setSize(w, h);
    composer.setSize(w, h);
    labels.setSize(w, h);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    draw();
  };
  const ro = new ResizeObserver(onResize);
  ro.observe(stage);
  const io = new IntersectionObserver((e) => setRunning(e[0].isIntersecting && !document.hidden));
  io.observe(stage);
  const onVis = () => setRunning(!document.hidden);
  document.addEventListener("visibilitychange", onVis);

  const teardown = () => {
    setRunning(false);
    ro.disconnect(); io.disconnect();
    document.removeEventListener("visibilitychange", onVis);
    controls.dispose();
    disposables.forEach((d) => d.dispose && d.dispose());
    composer.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  };
  window.addEventListener("pagehide", teardown, { once: true });
  renderer.domElement.style.cursor = "grab";
  document.body.classList.add("ready");
}

if (!hasWebGL()) {
  showFallback("WebGL is not available in this browser. Showing the static map.");
} else {
  try {
    await start();
  } catch (e) {
    console.error(e);
    showFallback("The 3D view could not load. Showing the static map.");
  }
}
