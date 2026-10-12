// 3D-план квартиры: комнаты, стены, предметы, плата/роутер, траектории, живой телефон, индикатор движения.
// Система плана: x вперёд (на роутер), y влево; в three.js: X = x, Z = -y, Y — вверх.
import * as THREE from "/static/vendor/three.module.min.js";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const WALL_H = 2.6;
const ROOM_COLORS = [0x4a90d9, 0xe0a43a, 0x5cb85c, 0xb36ad9, 0xd9644a, 0x4ac0c0];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const v3 = (x, y, h = 0) => new THREE.Vector3(x, h, -y);

const view = document.getElementById("view");
const note = document.getElementById("note");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
view.prepend(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 0.05, 200);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI / 2 - 0.02;
scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(6, 12, 4);
scene.add(sun);

let planGroup = new THREE.Group(), trajGroup = new THREE.Group();
scene.add(planGroup, trajGroup);
let bbox = { cx: 0, cy: 0, size: 8 };
let linkMat = null;

function applyTheme() {
  scene.background = new THREE.Color(css("--bg") || "#0f1216");
}
applyTheme();
matchMedia("(prefers-color-scheme: light)").addEventListener("change", applyTheme);

function label(text, color = "#e6e9ee", scale = 0.5) {
  const pad = 12, font = 44;
  const c = document.createElement("canvas");
  const g = c.getContext("2d");
  g.font = `600 ${font}px system-ui, sans-serif`;
  c.width = Math.ceil(g.measureText(text).width) + pad * 2;
  c.height = font + pad * 2;
  g.font = `600 ${font}px system-ui, sans-serif`;
  g.fillStyle = "rgba(15,18,22,0.72)";
  g.beginPath(); g.roundRect(0, 0, c.width, c.height, 14); g.fill();
  g.fillStyle = color; g.textBaseline = "middle";
  g.fillText(text, pad, c.height / 2 + 2);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  s.scale.set(scale * c.width / c.height, scale, 1);
  s.renderOrder = 10;
  return s;
}

function area(poly) {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[(i + poly.length - 1) % poly.length], [x1, y1] = poly[i];
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a) / 2;
}

function centroid(poly) {
  // центр масс многоугольника (для невыпуклых — может лежать снаружи; тогда берём среднее вершин)
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i], [x1, y1] = poly[(i + 1) % poly.length];
    const f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  if (Math.abs(a) < 1e-9) return poly[0];
  const c = [cx / (3 * a), cy / (3 * a)];
  return inside(c, poly) ? c : [poly.reduce((s, p) => s + p[0], 0) / poly.length, poly.reduce((s, p) => s + p[1], 0) / poly.length];
}

function inside([x, y], poly) {
  let r = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) r = !r;
  }
  return r;
}

function wall(a, b, mat, edgeMat) {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  if (len < 0.01) return null;
  const g = new THREE.Group();
  // плоскость без толщины: у дуг соседние сегменты не перекрываются и не дают полос
  const m = new THREE.Mesh(new THREE.PlaneGeometry(len, WALL_H), mat);
  m.position.set((a[0] + b[0]) / 2, WALL_H / 2, -(a[1] + b[1]) / 2);
  m.rotation.y = Math.atan2(b[1] - a[1], b[0] - a[0]);  // в three Z = -y
  g.add(m);
  const top = new THREE.BufferGeometry().setFromPoints([v3(a[0], a[1], WALL_H), v3(b[0], b[1], WALL_H)]);
  g.add(new THREE.Line(top, edgeMat));
  const base = new THREE.BufferGeometry().setFromPoints([v3(a[0], a[1], 0.01), v3(b[0], b[1], 0.01)]);
  g.add(new THREE.Line(base, edgeMat));
  return g;
}

function buildPlan(plan) {
  scene.remove(planGroup);
  planGroup = new THREE.Group();
  const wallMat = new THREE.MeshStandardMaterial({ color: 0xc9ced6, transparent: true, opacity: 0.28, depthWrite: false, side: THREE.DoubleSide });
  const edgeMat = new THREE.LineBasicMaterial({ color: 0xdfe3ea });
  const seen = new Set();
  const key = (p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`;
  const pts = [[0, 0]];
  plan.rooms.forEach((r, i) => {
    const poly = r.polygon;
    pts.push(...poly);
    const shape = new THREE.Shape(poly.map(([x, y]) => new THREE.Vector2(x, y)));
    const floor = new THREE.Mesh(new THREE.ShapeGeometry(shape),
      new THREE.MeshStandardMaterial({ color: ROOM_COLORS[i % ROOM_COLORS.length], transparent: true, opacity: 0.35, side: THREE.DoubleSide }));
    floor.rotation.x = -Math.PI / 2;
    planGroup.add(floor);
    for (let k = 0; k < poly.length; k++) {
      const a = poly[k], b = poly[(k + 1) % poly.length];
      const id = [key(a), key(b)].sort().join("|");
      if (seen.has(id)) continue; // общая стена двух комнат — одна
      seen.add(id);
      const w = wall(a, b, wallMat, edgeMat);
      if (w) planGroup.add(w);
    }
    const [cx, cy] = centroid(poly);
    const l = label(`${r.name} · ${area(poly).toFixed(1)} м²`, "#" + ROOM_COLORS[i % ROOM_COLORS.length].toString(16).padStart(6, "0"), 0.42);
    l.position.copy(v3(cx, cy, 0.25));
    planGroup.add(l);
  });
  for (const o of plan.objects || []) {
    const [w, d, h] = o.size || [0.5, 0.5, 0.8];
    const box = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color: 0x8d7b68 }));
    box.position.copy(v3(o.xy[0], o.xy[1], h / 2));
    planGroup.add(box);
    const l = label(o.type, "#e6e9ee", 0.3);
    l.position.copy(v3(o.xy[0], o.xy[1], h + 0.25));
    planGroup.add(l);
  }
  const marker = (xy, color, text, h) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.06, 0.12), new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.5 }));
    m.position.copy(v3(xy[0], xy[1], h));
    planGroup.add(m);
    const l = label(text, "#" + color.toString(16).padStart(6, "0"), 0.32);
    l.position.copy(v3(xy[0], xy[1], h + 0.35));
    planGroup.add(l);
  };
  marker(plan.board || [0, 0], 0xe0564f, "плата", 0.8);
  if (plan.router) {
    marker(plan.router, 0xb36ad9, "роутер B", 1.0);
    // линия связи плата–роутер: подсвечивается движением
    linkMat = new THREE.MeshBasicMaterial({ color: 0x3fb68b, transparent: true, opacity: 0.25 });
    const a = v3(0, 0, 0.8), b = v3(plan.router[0], plan.router[1], 1.0);
    const tube = new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(a, b), 8, 0.03, 8), linkMat);
    planGroup.add(tube);
  } else {
    linkMat = null;
  }
  scene.add(planGroup);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  bbox = {
    cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2,
    size: Math.max(4, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)),
  };
  buildTrajectories(plan);
  const edited = plan.rooms.some((r) => r.edited);
  note.textContent = plan.rooms.length
    ? `${plan.rooms.length} комн.${edited ? " · стены выправлены вручную" : ""}${plan.router ? "" : " · роутер не отмечен — оси произвольные"}`
    : "План пуст: запишите обход на странице «Съёмка».";
}

function buildTrajectories(plan) {
  scene.remove(trajGroup);
  trajGroup = new THREE.Group();
  Object.values(plan.trajectories || {}).forEach((tr, i) => {
    if (tr.length < 2) return;
    const g = new THREE.BufferGeometry().setFromPoints(tr.map(([, x, y]) => v3(x, y, 0.03)));
    trajGroup.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: [0xffffff, 0x9ad0ff, 0xffd59a][i % 3], transparent: true, opacity: 0.6 })));
  });
  trajGroup.visible = document.getElementById("traj").checked;
  scene.add(trajGroup);
}

function setView(mode) {
  const { cx, cy } = bbox;
  const size = bbox.size / Math.min(1, camera.aspect);  // узкий экран телефона — камера дальше
  controls.target.copy(v3(cx, cy, 0));
  if (mode === "top") camera.position.copy(v3(cx, cy - 0.001, size * 1.35));
  else camera.position.copy(v3(cx - size * 0.7, cy - size * 1.0, size * 1.05));
  controls.update();
  document.getElementById("top").setAttribute("aria-pressed", mode === "top");
  document.getElementById("persp").setAttribute("aria-pressed", mode !== "top");
}

async function loadPlan(resetView) {
  try {
    const r = await fetch("/api/plan", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    buildPlan(await r.json());
    if (resetView) setView("persp");
  } catch (e) {
    note.textContent = `Не удалось загрузить план: ${e.message}`;
  }
}

// ---- живые данные ----
const phone = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 14), new THREE.MeshStandardMaterial({ color: 0x5aa2ff, emissive: 0x5aa2ff, emissiveIntensity: 0.6 }));
phone.visible = false;
scene.add(phone);
let phoneSeen = 0, motionSeen = 0, energy = 0;
const fill = document.querySelector("#motion .fill"), val = document.querySelector("#motion .val");
const conn = document.getElementById("conn");

function onMotion(m) {
  energy = m.energy;
  motionSeen = performance.now();
  const frac = Math.max(0, Math.min(1, (m.energy - 0.5) / 3.5)); // 1 ≈ покой, ≥4 — сильное движение
  fill.style.width = `${(frac * 100).toFixed(0)}%`;
  fill.style.backgroundColor = m.energy < 1.6 ? css("--ok") : m.energy < 3 ? css("--warn") : css("--bad");
  val.textContent = `${m.energy.toFixed(2)} · ${m.energy < 1.6 ? "покой" : m.energy < 3 ? "движение" : "сильное"}`;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  ws.onopen = () => { conn.textContent = "онлайн"; conn.classList.remove("bad"); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "motion") onMotion(m);
    else if (m.type === "phone") {
      phone.position.copy(v3(m.xy[0], m.xy[1], 1.2));
      phone.visible = true;
      phoneSeen = performance.now();
    }
  };
  ws.onclose = () => {
    conn.textContent = "нет связи с сервером"; conn.classList.add("bad");
    setTimeout(connect, 2000);
  };
}

// ---- UI ----
document.getElementById("top").onclick = () => setView("top");
document.getElementById("persp").onclick = () => setView("persp");
document.getElementById("reload").onclick = () => loadPlan(false);
document.getElementById("traj").onchange = (e) => { trajGroup.visible = e.target.checked; };

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h);
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(view);

renderer.setAnimationLoop((t) => {
  const now = performance.now();
  if (phone.visible && now - phoneSeen > 3000) phone.visible = false;
  if (now - motionSeen > 3000 && motionSeen) { val.textContent = "нет данных"; fill.style.width = "0%"; motionSeen = 0; }
  if (linkMat) {
    const e = motionSeen ? Math.max(0, Math.min(1, (energy - 1) / 3)) : 0;
    linkMat.opacity = 0.2 + 0.6 * e * (0.75 + 0.25 * Math.sin(t / 120));
    linkMat.color.set(energy < 1.6 ? 0x3fb68b : energy < 3 ? 0xe0a43a : 0xe0564f);
  }
  controls.update();
  renderer.render(scene, camera);
});

resize();
loadPlan(true);
connect();
