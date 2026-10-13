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
const links = new Map(); // "плата|primary" | "плата|MAC передатчика" -> {mat, energy, seen, zone}
let mainDev = "";  // шкала движения — по основному каналу первой платы
const linkOf = (m) => {
  const k = m.primary ? "primary" : (m.src || "").toLowerCase();
  return links.get(`${(m.device_id || "").toLowerCase()}|${k}`) || links.get(`|${k}`);
};
const floors = new Map(); // комната/зона -> материал пола (подсветка «где движение»)
let zoneShapes = []; // [{name, poly}] — сначала зоны без стен (кухня), потом комнаты
const zoneOf = (xy) => (zoneShapes.find((z) => inside(xy, z.poly)) || {}).name || null;

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
  countSprites.clear();  // подписи числа людей были в старой группе плана
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
    floors.set(r.name, floor.material);
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
  for (const z of plan.zones || []) { // зона без стен (открытая кухня): плитка на полу и пунктирная граница
    const shape = new THREE.Shape(z.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
    const patch = new THREE.Mesh(new THREE.ShapeGeometry(shape),
      new THREE.MeshStandardMaterial({ color: 0xd8cfbd, transparent: true, opacity: 0.85, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1 }));
    patch.rotation.x = -Math.PI / 2;
    patch.position.y = 0.01;
    planGroup.add(patch);
    floors.set(z.name, patch.material);
    const outline = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(z.polygon.map(([x, y]) => v3(x, y, 0.02))),
      new THREE.LineDashedMaterial({ color: 0x9a8a6a, dashSize: 0.15, gapSize: 0.1 }));
    outline.computeLineDistances();
    planGroup.add(outline);
    const [cx, cy] = centroid(z.polygon);
    const l = label(`${z.name} · ${area(z.polygon).toFixed(1)} м²`, "#c9b48a", 0.36);
    l.position.copy(v3(cx, cy, 0.25));
    planGroup.add(l);
  }
  for (const o of plan.objects || []) {
    const [w, d, h] = o.size || [0.5, 0.5, 0.8];
    const box = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color: 0x8d7b68 }));
    const z0 = o.z || 0; // высота низа предмета над полом
    box.position.copy(v3(o.xy[0], o.xy[1], z0 + h / 2));
    if (o.type === "телевизор") box.material.color.set(0x15171a);
    box.rotation.y = o.rot || 0;  // поворот против часовой в системе плана
    if (o.type === "арка" || o.type === "дверь" || o.type === "окно") { box.material.transparent = true; box.material.opacity = 0.35; }
    planGroup.add(box);
    const l = label(o.type, "#e6e9ee", 0.3);
    l.position.copy(v3(o.xy[0], o.xy[1], z0 + h + 0.25));
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
  const boards = (plan.boards && plan.boards.length) ? plan.boards : [{ device_id: "", xy: plan.board || [0, 0], z: 0.8 }];
  mainDev = boards[0].device_id || "";
  boards.forEach((b, i) => marker(b.xy, 0xe0564f, boards.length > 1 ? `плата ${i + 1}` : "плата", b.z ?? 0.8));
  // линии связи «передатчик → плата»: каждая подсвечивается движением на своём канале
  zoneShapes = [...(plan.zones || []).map((z) => ({ name: z.name, poly: z.polygon })),
    ...plan.rooms.map((r) => ({ name: r.name, poly: r.polygon }))];
  links.clear();
  const addLink = (b, key, xy, h, name) => {
    const mat = new THREE.MeshBasicMaterial({ color: 0x3fb68b, transparent: true, opacity: 0.15 });
    const tube = new THREE.Mesh(new THREE.TubeGeometry(new THREE.LineCurve3(v3(b.xy[0], b.xy[1], b.z ?? 0.8), v3(xy[0], xy[1], h)), 8, 0.025, 8), mat);
    planGroup.add(tube);
    // зона передатчика: движение рядом с ним; breath — последнее найденное дыхание на этой линии
    links.set(`${(b.device_id || "").toLowerCase()}|${key}`, { mat, energy: 0, seen: 0, zone: zoneOf(xy), name, xy, h, breath: null, sprite: null });
  };
  if (plan.router) marker(plan.router, 0xb36ad9, "роутер B", 1.0);
  for (const b of boards) {
    if (plan.router) addLink(b, "primary", plan.router, 1.0, "роутер B");
    for (const o of plan.objects || []) if (o.mac) addLink(b, o.mac.toLowerCase(), o.xy, (o.z || 0) + (o.size?.[2] || 0.3), o.type);
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
  const size = bbox.size / Math.pow(Math.min(1, camera.aspect), 1.6);  // узкий экран телефона — камера дальше
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
const where = document.getElementById("where");

function onMotion(m) {
  const l = linkOf(m);
  if (l) { l.energy = m.energy; l.seen = performance.now(); }
  if (m.primary === false || (mainDev && (m.device_id || "").toLowerCase() !== mainDev)) return; // шкала — по основному каналу первой платы
  energy = m.energy;
  motionSeen = performance.now();
  const frac = Math.max(0, Math.min(1, (m.energy - 0.5) / 3.5)); // 1 ≈ покой, ≥4 — сильное движение
  fill.style.width = `${(frac * 100).toFixed(0)}%`;
  fill.style.backgroundColor = m.energy < 1.6 ? css("--ok") : m.energy < 3 ? css("--warn") : css("--bad");
  val.textContent = `${m.energy.toFixed(2)} · ${m.energy < 1.6 ? "покой" : m.energy < 3 ? "движение" : "сильное"}`;
}

const breathEl = document.getElementById("breath");
const BREATH_FRESH_MS = 45000;  // сервер присылает оценку раз в 15 с

function dropBreath(l) {
  if (l.sprite) { planGroup.remove(l.sprite); l.sprite = null; }
  l.breath = null;
}

function onBreath(m) {
  const l = linkOf(m);
  if (!l) return;
  dropBreath(l);
  if (!m.ok) return;
  l.breath = { bpm: m.bpm, t: performance.now() };
  l.sprite = label(`дыхание ${Math.round(m.bpm)}/мин`, "#9ad0ff", 0.34);
  l.sprite.position.copy(v3(l.xy[0], l.xy[1], l.h + 0.6));
  planGroup.add(l.sprite);
}

let pred = null;  // последний ответ модели (learner): зона, действие, уверенность
const ZONE_P_MIN = 0.5, MOVE_P_MIN = 0.5;

let predBoard = null;  // ответ модели на самой плате
const boardEl = document.getElementById("board");

function onPred(m) {
  if (m.source === "board") predBoard = { ...m, seen: performance.now() };
  else pred = { ...m, seen: performance.now() };
}

// ---- человек на плане (трекер сервера, сообщения "person") ----
const PERSON_FRESH_MS = 5000;
// фигура 1.7 м: таз -> корпус -> голова, плечи -> предплечья, бёдра -> голени; суставы — группы-шарниры
const figMat = new THREE.MeshStandardMaterial({ color: 0xff7a2f, emissive: 0xff7a2f, emissiveIntensity: 0.45, transparent: true, opacity: 0.95 });
function bone(len, r, up = false) {  // шарнир в начале кости, кость вниз (или вверх) по Y
  const j = new THREE.Group();
  const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, Math.max(0.01, len - 2 * r), 6, 12), figMat);
  m.position.y = up ? len / 2 : -len / 2;
  j.add(m);
  return j;
}
const fig = { root: new THREE.Group(), pelvis: new THREE.Group() };
fig.torso = bone(0.55, 0.15, true);
fig.head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 20, 14), figMat);
fig.head.position.y = 0.72;
fig.torso.add(fig.head);
fig.pelvis.add(fig.torso);
for (const [side, x] of [["L", 0.21], ["R", -0.21]]) {
  const sh = bone(0.3, 0.05); sh.position.set(x, 0.5, 0);
  const fa = bone(0.28, 0.045); fa.position.y = -0.3; sh.add(fa);
  fig.torso.add(sh);
  const th = bone(0.45, 0.07); th.position.set(x / 2, 0, 0);
  const sn = bone(0.45, 0.06); sn.position.y = -0.45; th.add(sn);
  fig.pelvis.add(th);
  Object.assign(fig, { ["sh" + side]: sh, ["fa" + side]: fa, ["th" + side]: th, ["sn" + side]: sn });
}
fig.root.add(fig.pelvis);
const discMat = new THREE.MeshBasicMaterial({ color: 0xff7a2f, transparent: true, opacity: 0.55, depthWrite: false, side: THREE.DoubleSide });
const disc = new THREE.Mesh(new THREE.CircleGeometry(0.35, 40), discMat);  // пятно под ногами: видно и сверху
disc.rotation.x = -Math.PI / 2; disc.position.y = 0.02;
const ringMat = new THREE.MeshBasicMaterial({ color: 0xff7a2f, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false });
const ring = new THREE.Mesh(new THREE.RingGeometry(0.92, 1.0, 48), ringMat);  // радиус = разброс оценки, м
ring.rotation.x = -Math.PI / 2; ring.position.y = 0.025;
const personGroup = new THREE.Group();
personGroup.add(fig.root, disc, ring);
personGroup.visible = false;
scene.add(personGroup);

// поза: углы суставов (рад) и высота таза; плавно подтягиваются к цели
const POSES = {
  "стоит": { h: 0.95, lie: 0, th: [0, 0], sn: [0, 0], sh: [0.05, 0.05], fa: [-0.1, -0.1] },
  "сидит": { h: 0.47, lie: 0, th: [-1.57, -1.57], sn: [1.57, 1.57], sh: [0.3, 0.3], fa: [-0.9, -0.9] },
  "лежит": { h: 0.45, lie: 1, th: [0, 0], sn: [0, 0], sh: [0.05, 0.05], fa: [-0.1, -0.1] },
};
const cur = JSON.parse(JSON.stringify(POSES["стоит"]));
let heading = 0;
function applyPose(name, walking, t, dt) {
  const tgt = JSON.parse(JSON.stringify(POSES[name] || POSES["стоит"]));
  if (walking) {  // шаг ~1.8 Гц: бёдра и руки в противофазе
    const ph = t * 0.0113;
    tgt.th = [-0.45 * Math.sin(ph), 0.45 * Math.sin(ph)];
    tgt.sn = [0.5 * Math.max(0, Math.sin(ph + 1.2)), 0.5 * Math.max(0, -Math.sin(ph + 1.2))];
    tgt.sh = [-0.35 * Math.sin(ph), 0.35 * Math.sin(ph)];  // рука — навстречу ноге своей стороны
  }
  const k = walking ? 1 : Math.min(1, dt / 0.25);
  cur.h += (tgt.h - cur.h) * k; cur.lie += (tgt.lie - cur.lie) * Math.min(1, dt / 0.25);
  for (const key of ["th", "sn", "sh", "fa"]) for (const i of [0, 1]) cur[key][i] += (tgt[key][i] - cur[key][i]) * k;
  fig.pelvis.position.y = cur.h;
  fig.pelvis.rotation.x = -cur.lie * Math.PI / 2;  // лёжа корпус вдоль пола
  fig.thL.rotation.x = cur.th[0]; fig.thR.rotation.x = cur.th[1];
  fig.snL.rotation.x = cur.sn[0]; fig.snR.rotation.x = cur.sn[1];
  fig.shL.rotation.x = -cur.sh[0]; fig.shR.rotation.x = -cur.sh[1];
  fig.faL.rotation.x = cur.fa[0]; fig.faR.rotation.x = cur.fa[1];
  fig.shL.rotation.z = 0.12; fig.shR.rotation.z = -0.12;
  fig.root.rotation.y = heading;
}
let person = null, personLabel = null, personLabelText = "", personPlaced = false, lastFrame = 0;
// «людей не меньше N»: в комнатах, где линии показывают движение, которое один человек не объясняет, — серые фигуры
const extraMat = new THREE.MeshStandardMaterial({ color: 0xb0b6c0, emissive: 0x6a7280, emissiveIntensity: 0.3, transparent: true, opacity: 0.8 });
const extras = new Map();  // комната -> {group, sprite}
function extraFigure() {
  const g = new THREE.Group();
  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(0.15, 0.6, 6, 12), extraMat); torso.position.y = 1.15;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 16, 12), extraMat); head.position.y = 1.62;
  const legs = new THREE.Mesh(new THREE.CapsuleGeometry(0.1, 0.7, 6, 12), extraMat); legs.position.y = 0.45;
  g.add(torso, head, legs);
  return g;
}
function drawExtras(rooms, mainZone) {
  const want = new Set((rooms || []).filter((r) => r !== mainZone));
  for (const [r, e] of extras) if (!want.has(r)) { scene.remove(e.group, e.sprite); extras.delete(r); }
  for (const r of want) {
    if (extras.has(r)) continue;
    const shape = zoneShapes.find((x) => x.name === r);
    if (!shape) continue;
    const [cx, cy] = centroid(shape.poly);
    const group = extraFigure(); group.position.copy(v3(cx, cy, 0));
    const sprite = label("ещё кто-то движется", "#c9ced6", 0.3); sprite.position.copy(v3(cx, cy, 2.0));
    scene.add(group, sprite);
    extras.set(r, { group, sprite });
  }
}
const personPos = new THREE.Vector3();
const personBox = document.getElementById("personBox");
const heatToggle = document.getElementById("heat");

// где может быть человек: клетки 0.25 м, яркость ~ вероятности
const HEAT_MAX = 80;
const heatMesh = new THREE.InstancedMesh(new THREE.PlaneGeometry(0.24, 0.24),
  new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.6, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }), HEAT_MAX);
const heatQ = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
const heatS = new THREE.Vector3(1, 1, 1), heatM = new THREE.Matrix4(), heatC = new THREE.Color(0, 0, 0);
for (let i = 0; i < HEAT_MAX; i++) heatMesh.setColorAt(i, heatC);
heatMesh.count = 0;
scene.add(heatMesh);

function onHeat(h) {
  const pmax = Math.max(1e-9, ...h.map((c) => c[2]));
  heatMesh.count = Math.min(HEAT_MAX, h.length);
  for (let i = 0; i < heatMesh.count; i++) {
    const [x, y, p] = h[i];
    const k = Math.sqrt(p / pmax);
    heatMesh.setMatrixAt(i, heatM.compose(v3(x, y, 0.03), heatQ, heatS));
    heatMesh.setColorAt(i, heatC.setRGB(0.12 * k, 0.4 * k, 1.0 * k));
  }
  heatMesh.instanceMatrix.needsUpdate = true;
  heatMesh.instanceColor.needsUpdate = true;
}

function onPerson(m) {
  person = { ...m, seen: performance.now() };
  if (m.heat) onHeat(m.heat);
}

// число движущихся людей по комнатам (многоголовая модель): подпись над полом комнаты
const countSprites = new Map();  // зона -> {sprite, text}
function drawCounts(counts) {
  const want = new Map(Object.entries(counts || {}).filter(([, n]) => n > 0));
  for (const [z, c] of countSprites) {
    if (!want.has(z) || c.text !== `${want.get(z)}`) { planGroup.remove(c.sprite); countSprites.delete(z); }
  }
  for (const [z, n] of want) {
    if (countSprites.has(z)) continue;
    const shape = zoneShapes.find((x) => x.name === z);
    if (!shape) continue;
    const [cx, cy] = centroid(shape.poly);
    const sp = label(`${n} чел. двигается`, "#ffb07a", 0.38);
    sp.position.copy(v3(cx, cy, 0.9));
    planGroup.add(sp);
    countSprites.set(z, { sprite: sp, text: `${n}` });
  }
}

const fmtDur = (s) => s < 90 ? `${Math.round(s)} с` : s < 5400 ? `${Math.round(s / 60)} мин` : `${(s / 3600).toFixed(1)} ч`;

function drawPerson(now, dt) {
  const live = person && now - person.seen < PERSON_FRESH_MS;
  heatMesh.visible = !!live && heatToggle.checked;
  drawCounts(live ? person.counts : null);
  drawExtras(live && person.min_people >= 2 ? person.min_rooms : [], live ? person.zone : null);
  if (!live || !person.present) {
    personGroup.visible = false;
    if (personLabel) personLabel.visible = false;
    personBox.textContent = !person ? "Человек: ждём данных трекера"
      : !live ? "Человек: нет свежих данных"
      : person.status === "вышел" ? `Человек вышел из квартиры: последнее движение у входной двери, ${fmtDur(person.still_s)} назад`
      : "Человек: ждём первого движения";
    return;
  }
  const target = v3(person.xy[0], person.xy[1], 0);
  if (!personPlaced) { personPos.copy(target); personPlaced = true; }
  const d = target.clone().sub(personPos), dist = d.length();
  // быстро к новой оценке: постоянная времени 0.2 с, без «прогулки» через всю квартиру
  const step = Math.min(dist, Math.max(dist * Math.min(1, dt / 0.2), 1.5 * dt));
  if (dist > 0.05) heading = Math.atan2(d.x, d.z);
  if (dist > 1e-6) personPos.add(d.multiplyScalar(step / dist));
  personGroup.position.copy(personPos);
  personGroup.visible = true;
  const sure = Math.max(0, Math.min(1, (person.zone_p - 0.4) / 0.4));
  const col = person.moving ? 0xff7a2f : 0x6fa8ff;
  figMat.color.set(col); figMat.emissive.set(col); ringMat.color.set(col); discMat.color.set(col);
  figMat.opacity = 0.55 + 0.4 * sure;
  // поза: идёт — по движению; сидит / лежит — оценка модели, только уверенная (точность в этой квартире не проверена)
  const walking = person.moving && /ход|ид/.test(person.activity || "");
  const modelPose = person.pose && person.pose.pose_p >= 0.55 && POSES[person.pose.pose] ? person.pose.pose : null;
  const poseName = walking ? "стоит" : modelPose || "стоит";
  applyPose(poseName, walking, now, dt);
  const r = Math.max(0.35, Math.min(2.0, person.spread_m || 0.5));
  ring.scale.set(r, r, 1);
  const pct = `${Math.round(100 * person.zone_p)}%`;
  const poseTxt = !walking && modelPose && modelPose !== "стоит" ? ` · ${modelPose}?` : "";
  const text = person.moving
    ? `${person.zone} ${pct} · ${person.activity}${poseTxt}`
    : `${person.zone} ${pct} · неподвижен ${fmtDur(person.still_s)}${poseTxt}${person.breath_bpm ? ` · дыхание ${Math.round(person.breath_bpm)}/мин` : ""}`;
  if (text !== personLabelText) {
    if (personLabel) scene.remove(personLabel);
    personLabel = label(text, person.moving ? "#ffb07a" : "#b9d3f0", 0.34);
    scene.add(personLabel);
    personLabelText = text;
  }
  personLabel.visible = true;
  personLabel.position.set(personPos.x, cur.lie > 0.5 ? 1.1 : 2.1, personPos.z);
  const src = Object.entries(person.sources || {}).filter(([, a]) => a < 3).map(([s]) => s === "board" ? "плата" : "сервер");
  const many = person.min_people >= 2 ? ` · людей не меньше ${person.min_people} (${person.min_rooms.join(", ")})` : "";
  // оценка числа людей моделью: на проверке точность низкая — показываем только уверенную и с пометкой
  const ppl = person.people && person.people.p >= 0.6 ? ` · модель: двигается ≈ ${person.people.n} (оценка, точность низкая)` : "";
  const ps = modelPose && !walking ? ` · поза «${modelPose}» — оценка модели, в этой квартире не проверена` : "";
  personBox.textContent = `Человек: ${text} · ±${(person.spread_m || 0).toFixed(1)} м · по ${src.join(" + ") || "—"}, линий с движением ${person.links_active}${many}${ppl}${ps}`;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  ws.onopen = () => { conn.textContent = "онлайн"; conn.classList.remove("bad"); };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "motion") onMotion(m);
    else if (m.type === "breath") onBreath(m);
    else if (m.type === "pred") onPred(m);
    else if (m.type === "person") onPerson(m);
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
  const dtFrame = lastFrame ? Math.min(0.1, (now - lastFrame) / 1000) : 0;
  lastFrame = now;
  drawPerson(now, dtFrame);
  const personLive = person && now - person.seen < PERSON_FRESH_MS && person.present;
  if (phone.visible && now - phoneSeen > 3000) phone.visible = false;
  if (now - motionSeen > 3000 && motionSeen) { val.textContent = "нет данных"; fill.style.width = "0%"; motionSeen = 0; }
  // «где движение»: для каждой зоны — максимум по линиям её передатчиков (энергия относительно своего фона)
  const act = new Map();
  for (const l of links.values()) {
    if (!l.zone || now - l.seen > 3000) continue;
    act.set(l.zone, Math.max(act.get(l.zone) || 0, l.energy));
  }
  let best = null;
  const live = pred && now - pred.seen < 3000;
  const moving = live && pred.moving_p >= MOVE_P_MIN;
  for (const [name, mat] of floors) {
    if (personLive) {  // трекер: подсвечиваем зоны по вероятности, что человек там
      const k = Math.max(0, Math.min(1, ((person.zones || {})[name] || 0) / 0.8));
      mat.emissive?.setRGB(0.1 * k, 0.3 * k, 0.8 * k);
      continue;
    }
    if (live) {  // модель: подсвечиваем её зону, если кто-то движется и она уверена
      const k = moving && name === pred.zone ? Math.max(0, Math.min(1, (pred.zone_p - 0.3) / 0.5)) : 0;
      mat.emissive?.setRGB(0.1 * k, 0.35 * k, 0.9 * k);
      continue;
    }
    const e = act.get(name) || 0;
    const k = Math.max(0, Math.min(1, (e - 1.4) / 2.0));
    mat.emissive?.setRGB(0.9 * k, 0.45 * k, 0.1 * k);
    if (e >= 1.6 && (!best || e > best[1])) best = [name, e];
  }
  if (live) {
    const pct = (x) => `${Math.round(100 * x)}%`;
    where.textContent = moving
      ? `· модель ${pred.model}: ${pred.zone_p >= ZONE_P_MIN ? pred.zone : "где-то"} ${pct(pred.zone_p)}, ${pred.activity} ${pct(pred.activity_p)}`
      : `· модель ${pred.model}: тихо (${pct(1 - pred.moving_p)})`;
  } else {
    where.textContent = act.size ? (best ? `· похоже: ${best[0]}` : "· тихо") : "";
  }
  if (predBoard && now - predBoard.seen < 3000) {
    const pct = (x) => `${Math.round(100 * x)}%`;
    boardEl.textContent = predBoard.moving_p >= MOVE_P_MIN
      ? `· плата: ${predBoard.zone} ${pct(predBoard.zone_p)}, ${predBoard.activity} (${predBoard.infer_ms} мс)`
      : `· плата: тихо (${predBoard.infer_ms} мс)`;
  } else boardEl.textContent = "";
  const br = [];
  for (const l of links.values()) {
    if (l.breath && now - l.breath.t > BREATH_FRESH_MS) dropBreath(l);
    if (l.breath) br.push(`${l.name} ${Math.round(l.breath.bpm)}/мин`);
  }
  breathEl.textContent = br.length ? `· дыхание: ${br.join(", ")}` : "";
  for (const l of links.values()) {
    const live = now - l.seen < 3000;
    const e = live ? Math.max(0, Math.min(1, (l.energy - 1) / 3)) : 0;
    l.mat.opacity = live ? 0.2 + 0.6 * e * (0.75 + 0.25 * Math.sin(t / 120)) : 0.08;
    l.mat.color.set(!live ? 0x888888 : l.energy < 1.6 ? 0x3fb68b : l.energy < 3 ? 0xe0a43a : 0xe0564f);
  }
  controls.update();
  renderer.render(scene, camera);
});

resize();
loadPlan(true);
connect();
