// Квартира: тепловая карта комнат по Wi-Fi. Цвет пола — сила движения в комнате (сообщения "rooms" раз в секунду),
// бирюзовое кольцо пульсирует с частотой найденного дыхания. Фигуры людей не рисуем: показываем то, что измеряется.
// План: x вперёд, y влево; в three.js X = x, Z = -y, Y — вверх.
import * as THREE from "/static/vendor/three.module.min.js";
import { OrbitControls } from "/static/vendor/OrbitControls.js";

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const v3 = (x, y, h = 0) => new THREE.Vector3(x, h, -y);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const fmtDur = (s) => s < 60 ? `${Math.round(s)} с` : s < 3600 ? `${Math.round(s / 60)} мин` : `${(s / 3600).toFixed(1).replace(".", ",")} ч`;

let C = {};
function readColors() {
  for (const k of ["bg", "floor", "wall", "warm", "hot", "breath", "text", "muted", "line"]) C[k] = new THREE.Color(css(`--${k}`));
}
readColors();

// ---- сцена ----
const planEl = $("plan");
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
planEl.prepend(renderer.domElement);
const scene = new THREE.Scene();
scene.background = C.bg.clone();
const camera = new THREE.OrthographicCamera(-5, 5, 5, -5, 0.1, 200);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI / 2.4;
controls.minZoom = 0.6; controls.maxZoom = 6;

let group = new THREE.Group();
scene.add(group);
scene.add(new THREE.HemisphereLight(0xffffff, 0x404858, 2.2));
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.position.set(-4, 10, 6);
scene.add(sun);
let bbox = { cx: 0, cy: 0, w: 8, h: 8 };
let tilted = true;
const rooms = new Map();   // имя -> {floor, glow, ring, c:[x,y], size, tag, row, motion, shown, breath, data}
const order = [];          // порядок комнат в списке: как на плане
const boardTags = [];

function glowTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, "rgba(255,255,255,1)"); grd.addColorStop(0.45, "rgba(255,255,255,0.55)"); grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const GLOW = glowTexture();

function centroid(poly) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i], [x1, y1] = poly[(i + 1) % poly.length];
    const f = x0 * y1 - x1 * y0; a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
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

function tag(cls, html) {
  const d = document.createElement("div"); d.className = `tag ${cls}`; d.innerHTML = html;
  planEl.append(d);
  return d;
}

function buildPlan(plan) {
  scene.remove(group); group = new THREE.Group(); scene.add(group);
  for (const r of rooms.values()) { r.tag.remove(); r.row?.remove(); }
  for (const t of boardTags) t.remove();
  rooms.clear(); order.length = 0; boardTags.length = 0;
  const wallMat = new THREE.MeshLambertMaterial({ color: C.wall });  // свет даёт стенам грани: тоньше на вид
  const seen = new Set();
  const key = (p) => `${p[0].toFixed(2)},${p[1].toFixed(2)}`;
  const pts = [];
  const addRoom = (name, poly, lift) => {
    pts.push(...poly);
    const shape = new THREE.Shape(poly.map(([x, y]) => new THREE.Vector2(x, y)));
    const floorMat = new THREE.MeshBasicMaterial({ color: C.floor.clone() });
    const floor = new THREE.Mesh(new THREE.ShapeGeometry(shape), floorMat);
    floor.rotation.x = -Math.PI / 2; floor.position.y = lift;
    group.add(floor);
    const c = centroid(poly);
    const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
    const size = Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: GLOW, color: C.warm.clone(), transparent: true, opacity: 0, depthWrite: false }));
    glow.rotation.x = -Math.PI / 2; glow.position.copy(v3(c[0], c[1], lift + 0.02));
    glow.scale.setScalar(Math.max(1.2, size * 1.1));
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.42, 0.5, 48),
      new THREE.MeshBasicMaterial({ color: C.breath.clone(), transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }));
    ring.rotation.x = -Math.PI / 2; ring.position.copy(v3(c[0], c[1], lift + 0.04));
    group.add(glow, ring);
    if (lift > 0) {  // зона без стен (кухня в зале): пунктирная граница
      const outline = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(poly.map(([x, y]) => v3(x, y, 0.03))),
        new THREE.LineDashedMaterial({ color: C.wall, dashSize: 0.18, gapSize: 0.12 }));
      outline.computeLineDistances();
      group.add(outline);
    }
    for (let k = 0; k < poly.length; k++) {
      const a = poly[k], b = poly[(k + 1) % poly.length];
      const id = [key(a), key(b)].sort().join("|");
      if (seen.has(id) || lift > 0) continue;  // общая стена — одна; у зон без стен (кухня) стен нет
      seen.add(id);
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (len < 0.01) continue;
      const w = new THREE.Mesh(new THREE.BoxGeometry(len + 0.04, 0.6, 0.04), wallMat);
      w.position.copy(v3((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.3));
      w.rotation.y = Math.atan2(b[1] - a[1], b[0] - a[0]);
      group.add(w);
    }
    const t = tag("", `<b>${cap(name)}</b><span>…</span>`);
    rooms.set(name, { floor, glow, ring, c, size, tag: t, motion: 0, shown: 0, breath: null, data: null });
    order.push(name);
  };
  for (const r of plan.rooms) addRoom(r.name, r.polygon, 0);
  for (const z of plan.zones || []) addRoom(z.name, z.polygon, 0.005);  // зона без стен (кухня в зале)
  const boards = (plan.boards && plan.boards.length) ? plan.boards : [{ device_id: "", xy: plan.board || [0, 0] }];
  boards.forEach((b) => {  // платы — точки с кольцом; подписи и состояние — в заголовке
    const m = new THREE.Mesh(new THREE.CircleGeometry(0.1, 24), new THREE.MeshBasicMaterial({ color: C.muted }));
    const o = new THREE.Mesh(new THREE.RingGeometry(0.16, 0.2, 32), new THREE.MeshBasicMaterial({ color: C.muted, side: THREE.DoubleSide }));
    for (const x of [m, o]) { x.rotation.x = -Math.PI / 2; x.position.copy(v3(b.xy[0], b.xy[1], 0.05)); group.add(x); }
  });
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  bbox = { cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2,
           w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  buildList();
  setView();
}

// ---- список комнат ----
function buildList() {
  const aside = $("rooms"); aside.replaceChildren();
  for (const name of order) {
    const r = rooms.get(name);
    const row = document.createElement("article");
    row.className = "room";
    row.innerHTML = `<div class="meter"><div style="height:0%"></div></div>
      <h2>${cap(name)}</h2><div class="state">ждём данных</div>
      <div class="breath" hidden><span class="dot"></span><span></span></div>
      <div class="src"></div><canvas height="28"></canvas>`;
    aside.append(row);
    r.row = row;
  }
}

function stateText(d) {
  if (d.motion >= 0.35) return "Движение сейчас";
  if (d.motion >= 0.15) return "Слабое движение";
  if (d.quiet_s == null) return "Движения не было";
  return d.quiet_s < 60 ? "Движение только что" : `Тихо ${fmtDur(d.quiet_s)}`;
}

function drawSpark(cv, hist) {
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w) return;
  cv.width = w * devicePixelRatio; cv.height = h * devicePixelRatio;
  const g = cv.getContext("2d"); g.scale(devicePixelRatio, devicePixelRatio);
  g.clearRect(0, 0, w, h);
  const n = hist.length; if (n < 2) return;
  const grad = g.createLinearGradient(0, h, 0, 0);
  grad.addColorStop(0, css("--warm")); grad.addColorStop(1, css("--hot"));
  g.beginPath(); g.moveTo(0, h);
  hist.forEach((v, i) => g.lineTo((i / (n - 1)) * w, h - Math.min(1, v) * (h - 2)));
  g.lineTo(w, h); g.closePath();
  g.globalAlpha = 0.85; g.fillStyle = grad; g.fill();
  g.globalAlpha = 1; g.fillStyle = css("--line"); g.fillRect(0, h - 1, w, 1);
}

function onRooms(m) {
  for (const d of m.rooms) {
    const r = rooms.get(d.name);
    if (!r) continue;
    r.data = d; r.motion = d.motion; r.breath = d.breath_bpm;
    const active = d.motion >= 0.35;
    r.tag.classList.toggle("active", active);
    r.tag.querySelector("span").textContent = active ? "движение"
      : d.quiet_s == null || d.quiet_s >= 3600 ? "тихо" : d.quiet_s < 60 ? "только что" : `тихо ${fmtDur(d.quiet_s)}`;
    if (!r.row) continue;
    r.row.classList.toggle("active", active);
    r.row.querySelector(".meter div").style.height = `${Math.round(Math.min(1, d.motion) * 100)}%`;
    r.row.querySelector(".state").textContent = stateText(d);
    const br = r.row.querySelector(".breath");
    br.hidden = !d.breath_bpm;
    if (d.breath_bpm) {
      br.lastElementChild.textContent = `Дыхание ${d.breath_bpm} в минуту`;
      br.style.setProperty("--period", `${(60 / d.breath_bpm).toFixed(2)}s`);
    }
    r.row.querySelector(".src").textContent = d.source === "board" ? "В комнате своя плата" : "Оценка по линиям, примерная";
    drawSpark(r.row.querySelector("canvas"), d.hist || []);
  }
  lastRooms = performance.now();
}

// ---- положение (по желанию): оценка трекера, только уверенная ----
let person = null, showWho = false;
const who = new THREE.Mesh(new THREE.CircleGeometry(0.28, 40), new THREE.MeshBasicMaterial({ color: C.hot.clone(), transparent: true, opacity: 0.9 }));
who.rotation.x = -Math.PI / 2; who.visible = false; scene.add(who);
const whoTag = tag("", "<b>движется здесь</b><span>оценка</span>"); whoTag.hidden = true;
$("who").onclick = (e) => { showWho = !showWho; e.currentTarget.setAttribute("aria-pressed", showWho); };

// ---- платы ----
async function pollBoards(plan) {
  try {
    const d = await (await fetch("/api/devices", { cache: "no-store" })).json();
    const live = Object.fromEntries(d.devices.map((x) => [x.device_id, x]));
    const list = (plan.boards || []).map((b, i) => {
      const x = live[b.device_id];
      const on = x && x.age_s < 5;
      const room = Object.entries(planRooms).find(([, poly]) => inside([b.xy[0], b.xy[1]], poly))?.[0] || "";
      return `<span class="board" title="${on ? `${Math.round(x.csi_hz)} кадров в секунду` : "нет данных от платы"}"><span class="dot ${on ? "on" : "off"}"></span>Плата ${i + 1}${room ? `, ${room}` : ""}${on ? "" : ": не в сети"}</span>`;
    });
    $("boards").innerHTML = list.join("");
  } catch (e) { /* сервер недоступен — об этом скажет строка состояния */ }
}
let planRooms = {};  // имя -> многоугольник

// ---- связь ----
let lastRooms = 0, wsOpen = false;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
  ws.onopen = () => { wsOpen = true; };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "rooms") onRooms(m);
    else if (m.type === "person") person = { ...m, seen: performance.now() };
  };
  ws.onclose = () => { wsOpen = false; setTimeout(connect, 2000); };
}

// ---- вид ----
function setView() {
  const aspect = planEl.clientWidth / Math.max(1, planEl.clientHeight);
  const half = Math.max(bbox.h / 2, bbox.w / 2 / aspect) + 0.8;
  camera.left = -half * aspect; camera.right = half * aspect; camera.top = half; camera.bottom = -half;
  camera.zoom = 1; camera.updateProjectionMatrix();
  const target = v3(bbox.cx, bbox.cy, 0);
  controls.target.copy(target);
  camera.up.set(0, 1, 0);
  if (tilted) camera.position.copy(target.clone().add(new THREE.Vector3(-6, 18, 16)));
  else camera.position.copy(target.clone().add(new THREE.Vector3(0, 30, 0.001)));
  controls.update();
  $("view").textContent = tilted ? "Сверху" : "Под углом";
}
$("view").onclick = () => { tilted = !tilted; setView(); };

function resize() {
  renderer.setSize(planEl.clientWidth, planEl.clientHeight, false);
  setView();
}
new ResizeObserver(resize).observe(planEl);
matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { readColors(); scene.background = C.bg.clone(); loadPlan(); });

const tmp = new THREE.Vector3();
function place(el, x, y, h) {
  tmp.copy(v3(x, y, h)).project(camera);
  el.style.left = `${(tmp.x * 0.5 + 0.5) * planEl.clientWidth}px`;
  el.style.top = `${(-tmp.y * 0.5 + 0.5) * planEl.clientHeight}px`;
}

const heat = new THREE.Color();
let last = performance.now();
renderer.setAnimationLoop((t) => {
  const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000); last = now;
  for (const r of rooms.values()) {
    r.shown += (r.motion - r.shown) * Math.min(1, dt / 0.35);
    const m = Math.max(0, Math.min(1, r.shown));
    if (m < 0.5) heat.lerpColors(C.floor, C.warm, m * 2); else heat.lerpColors(C.warm, C.hot, (m - 0.5) * 2);
    r.floor.material.color.lerpColors(C.floor, heat, Math.min(1, m * 1.6));
    r.glow.material.color.copy(m < 0.5 ? C.warm : C.hot);
    r.glow.material.opacity = Math.min(0.9, m * 1.4);
    if (r.breath) {
      const ph = reduceMotion ? 0.5 : (Math.sin((now / 1000) * 2 * Math.PI * (r.breath / 60)) + 1) / 2;
      r.ring.material.opacity = 0.35 + 0.5 * ph;
      r.ring.scale.setScalar(0.85 + 0.35 * ph);
    } else r.ring.material.opacity = 0;
    place(r.tag, r.c[0], r.c[1], 0.5);
  }
  for (const b of boardTags) place(b, +b.dataset.x, +b.dataset.y + 0.3, 0.5);
  const okWho = showWho && person && now - person.seen < 5000 && person.present && person.moving && person.zone_p >= 0.7;
  who.visible = !!okWho; whoTag.hidden = !okWho;
  if (okWho) {
    who.position.lerp(v3(person.xy[0], person.xy[1], 0.08), Math.min(1, dt / 0.2));
    place(whoTag, who.position.x, -who.position.z, 1.2);
  }
  $("notice").textContent = !wsOpen ? "Нет связи с сервером, переподключаюсь…"
    : now - lastRooms > 5000 ? "Ждём данных от плат…" : "";
  controls.update();
  renderer.render(scene, camera);
});

let plan = null;
async function loadPlan() {
  try {
    plan = await (await fetch("/api/plan", { cache: "no-store" })).json();
    planRooms = Object.fromEntries([...plan.rooms.map((r) => [r.name, r.polygon]), ...(plan.zones || []).map((z) => [z.name, z.polygon])]);
    buildPlan(plan);
    pollBoards(plan);
  } catch (e) {
    $("notice").textContent = `Не удалось загрузить план: ${e.message}`;
  }
}
await loadPlan();
setInterval(() => plan && pollBoards(plan), 5000);
resize();
connect();
