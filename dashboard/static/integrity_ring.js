// Audit-chain integrity ring (ES module, inlined by dashboard/three_scene.py).
// One segment per link in the hash chain. Signal blue when the chain verifies, alarm red from the
// first bad link when it does not.
const DATA = JSON.parse(document.getElementById("scene-data").textContent);
const T = DATA.theme;
const hex = (v) => parseInt(v.replace("#", ""), 16);
const ACCENT = hex(T.accent);
const ALARM = hex(T.alarm);
const VOID = hex(T.bg);
const stage = document.getElementById("stage");
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function hasWebGL() {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch (e) {
    return false;
  }
}

async function start() {
  const base = "three/addons/";
  const [THREE, ec, rp, ub, op] = await Promise.all([
    import("three"),
    import(base + "postprocessing/EffectComposer.js"),
    import(base + "postprocessing/RenderPass.js"),
    import(base + "postprocessing/UnrealBloomPass.js"),
    import(base + "postprocessing/OutputPass.js"),
  ]);
  const w = () => Math.max(stage.clientWidth, 10);
  const h = () => Math.max(stage.clientHeight, 10);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(w(), h());
  renderer.setClearColor(VOID, 1);
  stage.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(VOID);
  const camera = new THREE.PerspectiveCamera(38, w() / h(), 0.1, 50);
  camera.position.set(0, 3.4, 8.4);
  camera.lookAt(0, -0.7, 0);
  camera.zoom = reduced ? 1 : 0.6;   // eased to 1 on load
  camera.updateProjectionMatrix();

  const composer = new ec.EffectComposer(renderer);
  composer.setSize(w(), h());
  composer.addPass(new rp.RenderPass(scene, camera));
  if (T.bloom) {
    composer.addPass(new ub.UnrealBloomPass(new THREE.Vector2(w(), h()), 0.3, 0.3, 0.55));
  }
  composer.addPass(new op.OutputPass());

  const ok = !!DATA.ok;
  const n = 40;
  const bad = ok ? n : Math.min(n - 1, Math.max(0, DATA.bad_index));
  const disposables = [];
  const rig = new THREE.Group();
  scene.add(rig);
  const geo = new THREE.BoxGeometry(0.2, 0.1, 0.5);
  disposables.push(geo);
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2;
    const flagged = !ok && i >= bad;
    const mat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(flagged ? ALARM : ACCENT).multiplyScalar(T.additive ? (flagged ? 1.5 : 0.95) : 1) });
    disposables.push(mat);
    const m = new THREE.Mesh(geo, mat);
    m.position.set(Math.cos(a) * 2.6, 0, Math.sin(a) * 2.6);
    m.rotation.y = -a;
    rig.add(m);
  }
  const inner = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.15, 1)),
    new THREE.LineBasicMaterial({ color: ok ? ACCENT : ALARM }));
  disposables.push(inner.geometry, inner.material);
  scene.add(inner);
  rig.rotation.x = 0.35;

  let raf = 0;
  const clock = new THREE.Clock();
  const draw = () => {
    const t = clock.getElapsedTime();
    if (!reduced) {
      if (t < 1.8) {
        camera.zoom = 0.6 + 0.4 * (1 - Math.pow(1 - Math.min(1, t / 1.6), 3));
        camera.updateProjectionMatrix();
      }
      rig.rotation.y = t * 0.18;
      inner.rotation.y = -t * 0.3;
      inner.rotation.x = t * 0.12;
      if (!ok) inner.position.x = Math.sin(t * 38) * 0.015;
    }
    composer.render();
  };
  const frame = () => { raf = requestAnimationFrame(frame); draw(); };
  const ro = new ResizeObserver(() => {
    renderer.setSize(w(), h());
    composer.setSize(w(), h());
    camera.aspect = w() / h();
    camera.updateProjectionMatrix();
    draw();
  });
  ro.observe(stage);
  const onVis = () => {
    cancelAnimationFrame(raf);
    if (!document.hidden && !reduced) frame();
  };
  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("pagehide", () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    disposables.forEach((d) => d.dispose());
    composer.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }, { once: true });
  draw();
  if (!reduced) frame();
  document.body.classList.add("ready");
}

if (hasWebGL()) {
  try {
    await start();
  } catch (e) {
    console.error(e);
    document.body.classList.add("nogl");
  }
} else {
  document.body.classList.add("nogl");
}
