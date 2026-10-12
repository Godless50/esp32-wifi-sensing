"use strict";
// Phone capture page: WebXR immersive-ar + DOM overlay. Streams poses (30 Hz) and events over /ws/capture.
// Unacknowledged poses/events stay in memory and are resent after reconnect; the server drops duplicates.

const $ = (id) => document.getElementById(id);
const POSE_HZ = 30, SEND_MS = 200, PING_MS = 5000, MAX_BATCH = 300, MAX_PENDING = 30 * 60 * 10;

const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* приватный режим */ } },
};
let clientId = store.get("wsn.client_id", "");
if (!clientId) { clientId = "phone-" + Math.random().toString(36).slice(2, 10); store.set("wsn.client_id", clientId); }

const st = {
  ws: null, online: false, session: null, owner: false, csiHz: 0, clockSynced: false,
  poseSeq: 0, eventSeq: 0, ackPose: 0, ackEvent: 0,
  pendingPoses: [], pendingEvents: [],
  xr: null, gl: null, layer: null, floor: null, hitSource: null,
  viewerPos: null, viewerRot: null, emulated: true, hitPos: null, lastPoseT: 0,
  roomOpen: null, label: "walk",
};

// ---------- log ----------
function log(text, cls = "") {
  const box = $("log");
  const d = document.createElement("div");
  d.textContent = text;
  if (cls) d.className = cls;
  box.prepend(d);
  while (box.children.length > 3) box.lastChild.remove();
  setTimeout(() => d.remove(), 6000);
}
const ERRORS = {
  session_busy: "Уже идёт запись с другого телефона.", not_owner: "Запись ведёт другой телефон.",
  no_session: "Запись не идёт.", no_clock_sync: "Синхронизирую время, повторите через секунду.",
  no_device: "Плата не передаёт CSI — проверьте её.", bad_polygon: "Комната не замкнута: ",
  bad_router: "Роутер: ", no_room: "Сначала «Новая комната».", bad_session: "Не удалось начать запись: ",
};

// ---------- websocket ----------
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/capture`);
  st.ws = ws;
  ws.onopen = () => {
    st.online = true;
    ws.send(JSON.stringify({ type: "hello", client_id: clientId }));
    ping();
    render();
  };
  ws.onmessage = (ev) => onServer(JSON.parse(ev.data));
  ws.onclose = () => { st.online = false; st.clockSynced = false; render(); setTimeout(connect, 2000); };
  ws.onerror = () => ws.close();
}
function send(obj) { if (st.ws && st.ws.readyState === 1) st.ws.send(JSON.stringify(obj)); }
function ping() { send({ type: "ping", c0: performance.now() }); }

function onServer(m) {
  if (m.type === "pong") {
    send({ type: "sync", c0: m.c0, s: m.s, c1: performance.now() });
  } else if (m.type === "ack") {
    st.ackPose = Math.max(st.ackPose, m.pose_seq);
    st.ackEvent = Math.max(st.ackEvent, m.event_seq);
    // после перезагрузки страницы продолжаем нумерацию с того, что сервер уже видел
    st.poseSeq = Math.max(st.poseSeq, m.pose_seq);
    st.eventSeq = Math.max(st.eventSeq, m.event_seq);
    st.pendingPoses = st.pendingPoses.filter((p) => p.seq > st.ackPose);
    st.pendingEvents = st.pendingEvents.filter((e) => e.seq > st.ackEvent);
  } else if (m.type === "state") {
    st.session = m.session; st.owner = !!m.owner; st.clockSynced = !!m.clock_synced;
    if (typeof m.csi_hz === "number") st.csiHz = m.csi_hz;
    if (!st.session) st.roomOpen = null;
  } else if (m.type === "error") {
    const base = ERRORS[m.code];
    log(base ? (base.endsWith(": ") ? base + m.message : base) : m.message, "err");
  }
  render();
}

// ---------- events ----------
function event(kind, extra = {}) {
  const e = { type: "event", seq: ++st.eventSeq, t: performance.now(), kind, ...extra };
  st.pendingEvents.push(e);
  send(e);
  return e;
}
function resendEvents() { for (const e of st.pendingEvents) send(e); }

function needPose() {
  if (!st.viewerPos) { log("Нет позиции телефона — поводите камерой.", "err"); return null; }
  if (st.emulated) log("Внимание: трекинг только по повороту, позиция неточна.", "err");
  return [st.viewerPos.x, st.viewerPos.y, st.viewerPos.z];
}

// ---------- pose stream ----------
setInterval(() => {
  if (!st.online) return;
  const batch = st.pendingPoses.filter((p) => p.seq > st.ackPose).slice(0, MAX_BATCH);
  if (batch.length) send({ type: "poses", items: batch });
}, SEND_MS);
setInterval(() => { if (st.online) { ping(); resendEvents(); } }, PING_MS);

function recording() { return st.session && st.owner && st.session.kind === "walk"; }

function onXRFrame(t, frame) {
  const s = st.xr;
  if (!s) return;
  s.requestAnimationFrame(onXRFrame);
  const gl = st.gl;
  gl.bindFramebuffer(gl.FRAMEBUFFER, st.layer.framebuffer); // прозрачный кадр: иначе Chrome может не отдавать позы
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  const pose = frame.getViewerPose(st.floor);
  if (pose) {
    st.viewerPos = pose.transform.position;
    st.viewerRot = pose.transform.orientation;
    st.emulated = !!pose.emulatedPosition;
    if (recording() && !st.emulated && t - st.lastPoseT >= 1000 / POSE_HZ - 1) {
      st.lastPoseT = t;
      const p = st.viewerPos, q = st.viewerRot;
      st.pendingPoses.push({ seq: ++st.poseSeq, t, p: [p.x, p.y, p.z], q: [q.x, q.y, q.z, q.w] });
      if (st.pendingPoses.length > MAX_PENDING) st.pendingPoses.splice(0, st.pendingPoses.length - MAX_PENDING);
    }
  }
  st.hitPos = null;
  if (st.hitSource) {
    const hits = frame.getHitTestResults(st.hitSource);
    if (hits.length) st.hitPos = hits[0].getPose(st.floor).transform.position;
  }
  $("cross").classList.toggle("nohit", !st.hitPos);
}

async function enterXR() {
  let session;
  try {
    session = await navigator.xr.requestSession("immersive-ar", {
      requiredFeatures: ["local-floor"],
      optionalFeatures: ["hit-test", "dom-overlay"],
      domOverlay: { root: $("ui") },
    });
  } catch (e) { $("support").textContent = "Не удалось запустить AR: " + e.message; return; }
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2", { xrCompatible: true, alpha: true }) ||
             canvas.getContext("webgl", { xrCompatible: true, alpha: true });
  st.gl = gl;
  st.layer = new XRWebGLLayer(session, gl, { alpha: true });
  session.updateRenderState({ baseLayer: st.layer });
  st.floor = await session.requestReferenceSpace("local-floor");
  try {
    const viewer = await session.requestReferenceSpace("viewer");
    st.hitSource = await session.requestHitTestSource({ space: viewer });
  } catch { st.hitSource = null; log("Поиск пола недоступен: углы по положению телефона.", "err"); }
  st.xr = session;
  $("ui").classList.add("on");
  session.addEventListener("end", () => { st.xr = null; $("ui").classList.remove("on"); render(); });
  session.requestAnimationFrame(onXRFrame);
  render();
}

// ---------- buttons ----------
$("bWalk").onclick = () => event("session_start", { session_kind: "walk" });
$("bEmpty").onclick = () => {
  const dur = Number($("emptyDur").value);
  event("session_start", { session_kind: "empty", duration_s: dur });
  log(`Пустая квартира: ${dur / 60} мин. Уходите, запись остановится сама.`, "ok");
};
$("bStop").onclick = () => event("session_stop");
$("bOrigin").onclick = () => { const p = needPose(); if (p) { event("origin", { p }); log("Плата отмечена.", "ok"); } };
$("bRouter").onclick = () => { const p = needPose(); if (p) { event("router", { p }); log("Роутер отмечен.", "ok"); } };
$("bRoom").onclick = () => {
  const name = $("roomName").value.trim() || `Комната ${new Date().toLocaleTimeString()}`;
  event("room_new", { name });
  st.roomOpen = { name, corners: 0 };
  log(`Комната «${name}»: отмечайте углы на полу.`, "ok");
};
$("bCorner").onclick = () => {
  let p, source = "hit";
  if (st.hitPos) p = [st.hitPos.x, st.hitPos.y, st.hitPos.z];
  else { p = needPose(); source = "phone"; if (p) p[1] = 0; }
  if (!p) return;
  event("corner", { p, source });
  if (st.roomOpen) st.roomOpen.corners++;
  log(`Угол ${st.roomOpen ? st.roomOpen.corners : ""}${source === "phone" ? " (по телефону)" : ""}.`, "ok");
};
$("bClose").onclick = () => { event("room_close"); st.roomOpen = null; };
$("bObject").onclick = () => {
  const p = st.hitPos ? [st.hitPos.x, st.hitPos.y, st.hitPos.z] : needPose();
  if (!p) return;
  const type = $("objType").value;
  event("object", { p, type, size: { "стол": [1.2, 0.8, 0.75], "диван": [2, 0.9, 0.8], "кровать": [2, 1.6, 0.5],
    "шкаф": [1, 0.6, 2], "дверь": [0.9, 0.1, 2] }[type] || [0.5, 0.5, 0.8] });
  log(`Предмет: ${type}.`, "ok");
};
for (const b of document.querySelectorAll("[data-pose]")) {
  b.onclick = () => {
    st.label = b.dataset.pose;
    event("label", { pose: st.label });
    for (const x of document.querySelectorAll("[data-pose]")) x.classList.toggle("sel", x === b);
  };
}

// ---------- render ----------
function fmtDur(s) { const m = Math.floor(s / 60); return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`; }
function render() {
  $("conn").textContent = st.online ? (st.clockSynced ? "онлайн" : "синхронизация…") : "нет связи";
  $("dotConn").className = "dot " + (st.online ? "ok" : "bad");
  $("conn0").textContent = st.online ? "есть" : "нет";
  const rec = st.session && st.owner;
  $("rec").textContent = !st.session ? "не пишем" : rec ? (st.session.kind === "empty" ? "пусто" : "запись") : "пишет другой телефон";
  $("dotRec").className = "dot " + (rec ? "rec" : "");
  $("hz").textContent = st.csiHz ? st.csiHz.toFixed(0) : "—";
  $("queue").textContent = String(st.pendingPoses.length + st.pendingEvents.length);
  $("room").textContent = st.roomOpen ? `«${st.roomOpen.name}»: углов ${st.roomOpen.corners}` : "";
  $("bWalk").disabled = !!st.session;
  $("emptyDur").disabled = !!st.session;
  $("bEmpty").disabled = !!st.session;
  $("bStop").disabled = !rec;
  for (const el of document.querySelectorAll("[data-needrec]")) el.disabled = !(rec && st.session.kind === "walk");
}
setInterval(() => {
  if (st.session && st.owner) {
    const started = st.session.started_wall * 1000;
    $("dur").textContent = fmtDur((Date.now() - started) / 1000);
  } else {
    $("dur").textContent = "";
  }
  render();
}, 1000);

// ---------- start ----------
async function init() {
  connect();
  if (!window.isSecureContext) { $("support").textContent = "Нужен HTTPS с установленным сертификатом (/ca.crt)."; return; }
  if (!navigator.xr) { $("support").textContent = "WebXR недоступен в этом браузере. Нужен Chrome на Android с ARCore."; return; }
  let ok = false;
  try { ok = await navigator.xr.isSessionSupported("immersive-ar"); } catch { ok = false; }
  if (!ok) { $("support").innerHTML = 'AR не поддерживается. Проверка: <a href="/capture/probe">/capture/probe</a>'; return; }
  $("support").textContent = "AR поддерживается.";
  $("enter").disabled = false;
  $("enter").onclick = enterXR;
}
init();
