// Сценарий записи: телефон голосом ведёт по точкам и ставит точные метки (/api/labels, script + subject).
const $ = (id) => document.getElementById(id);
const ZONES = ["зал", "кухня", "коридор", "санузел", "спальня", "комната"];
const POINTS = [
  { zone: "зал", place: "у телевизора", on: true },
  { zone: "зал", place: "у окна", on: true },
  { zone: "кухня", place: "у плиты", on: true },
  { zone: "кухня", place: "у холодильника", on: true },
  { zone: "коридор", place: "у входной двери", on: true },
  { zone: "коридор", place: "у арки", on: true },
  { zone: "санузел", place: "в санузле", on: true },
  { zone: "спальня", place: "у двери спальни", on: true },
  { zone: "спальня", place: "у кровати", on: true },
  { zone: "спальня", place: "у окна спальни", on: true },
  { zone: "комната", place: "у двери комнаты", on: true },
  { zone: "комната", place: "в середине комнаты", on: true },
  { zone: "зал", place: "в середине зала", on: false },
  { zone: "зал", place: "у дивана", on: false },
  { zone: "кухня", place: "у стола", on: false },
  { zone: "коридор", place: "в середине коридора", on: false },
];
const ACTS = [
  { act: "стоит", say: "Стой спокойно", text: "Стой спокойно", label: "стоит", sec: 30, on: true },
  { act: "машет", say: "Маши руками", text: "Маши руками", label: "машет руками", sec: 20, on: true },
  { act: "ходит", say: "Ходи вокруг этого места", text: "Ходи вокруг", label: "ходит", sec: 30, on: true },
  { act: "мелкие", say: "Стой и печатай на телефоне", text: "Печатай на телефоне", label: "печатает на телефоне", sec: 20, on: true },
];
const REST = { act: "покой", say: "Ляг и не двигайся", text: "Лежи неподвижно", label: "лежит на диване", sec: 60 };
const DEFAULT_OTHERS = { "зал": [1, "лежит на диване"], "спальня": [1, "спит"], "комната": [2, "спят"] };
const KEY = "wsn-scenario-v1";

class Cancelled extends Error {}

let cfg = loadCfg();
let run = null;  // { cancel, sid, mine, labels }
let wakeLock = null;
let actx = null;
let ruVoice = null;

function loadCfg() {
  const base = {
    others: Object.fromEntries(ZONES.map((z) => [z, DEFAULT_OTHERS[z] || [0, ""]])),
    points: POINTS.map((p) => p.on), acts: ACTS.map((a) => a.on), secs: ACTS.map((a) => a.sec),
    moveSec: 12, shuffle: true, shuffleActs: true, rest: true,
  };
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    if (saved && saved.points?.length === POINTS.length && saved.acts?.length === ACTS.length) return { ...base, ...saved };
  } catch (e) { /* хранилище недоступно — берём умолчания */ }
  return base;
}

function saveCfg() {
  try { localStorage.setItem(KEY, JSON.stringify(cfg)); } catch (e) { /* не критично */ }
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k in e) e[k] = v; else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

function renderSettings() {
  const others = $("others"); others.replaceChildren();
  for (const z of ZONES) {
    const [n, act] = cfg.others[z] || [0, ""];
    const num = el("input", { type: "number", min: 0, max: 9, value: n });
    const txt = el("input", { type: "text", value: act, maxLength: 30, placeholder: "что делает" });
    const upd = () => { cfg.others[z] = [Math.max(0, Math.min(9, parseInt(num.value, 10) || 0)), txt.value.trim()]; changed(); };
    num.addEventListener("input", upd); txt.addEventListener("input", upd);
    others.append(el("div", { class: "row" }, el("span", { class: "name" }, z), num, txt));
  }
  const pts = $("points"); pts.replaceChildren();
  POINTS.forEach((p, i) => {
    const cb = el("input", { type: "checkbox", checked: cfg.points[i] });
    cb.addEventListener("change", () => { cfg.points[i] = cb.checked; changed(); });
    pts.append(el("label", { class: "check" }, cb, `${p.zone} · ${p.place}`));
  });
  const acts = $("acts"); acts.replaceChildren();
  ACTS.forEach((a, i) => {
    const cb = el("input", { type: "checkbox", checked: cfg.acts[i] });
    const sec = el("input", { type: "number", min: 10, max: 120, value: cfg.secs[i] });
    cb.addEventListener("change", () => { cfg.acts[i] = cb.checked; changed(); });
    sec.addEventListener("input", () => { cfg.secs[i] = Math.max(10, Math.min(120, parseInt(sec.value, 10) || a.sec)); changed(); });
    acts.append(el("div", { class: "row" }, el("label", { class: "check", style: "flex:1" }, cb, a.text), sec));
  });
  for (const id of ["shuffle", "shuffleActs", "rest"]) {
    $(id).checked = !!cfg[id];
    $(id).onchange = () => { cfg[id] = $(id).checked; changed(); };
  }
  $("moveSec").value = cfg.moveSec;
  $("moveSec").oninput = () => { cfg.moveSec = Math.max(5, Math.min(60, parseInt($("moveSec").value, 10) || 12)); changed(); };
  changed();
}

function changed() {
  saveCfg();
  const steps = buildPlan(false);
  const sec = steps.reduce((s, x) => s + x.sec, 0);
  $("total").textContent = steps.length ? `· ${steps.length} шагов, ≈ ${Math.ceil(sec / 60)} мин` : "· выбери точки и действия";
  $("start").disabled = !steps.some((s) => s.kind === "act") || !!run;
}

function shuffled(a) {
  const b = a.slice();
  for (let i = b.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [b[i], b[j]] = [b[j], b[i]]; }
  return b;
}

function buildPlan(randomize = true) {
  let pts = POINTS.filter((_, i) => cfg.points[i]);
  if (randomize && cfg.shuffle) pts = shuffled(pts);
  const acts = ACTS.map((a, i) => ({ ...a, sec: cfg.secs[i] })).filter((_, i) => cfg.acts[i]);
  const steps = [];
  if (!acts.length) return steps;
  for (const p of pts) {
    steps.push({ kind: "move", zone: p.zone, place: p.place, sec: cfg.moveSec });
    for (const a of (randomize && cfg.shuffleActs ? shuffled(acts) : acts)) steps.push({ kind: "act", zone: p.zone, place: p.place, ...a });
  }
  if (cfg.rest && steps.length) {
    steps.push({ kind: "move", zone: "зал", place: "к дивану", sec: cfg.moveSec });
    steps.push({ kind: "act", zone: "зал", place: "на диване", ...REST });
  }
  return steps;
}

function pickVoice() {
  try {
    const vs = speechSynthesis.getVoices();
    ruVoice = vs.find((v) => v.lang?.toLowerCase().startsWith("ru") && v.localService) || vs.find((v) => v.lang?.toLowerCase().startsWith("ru")) || null;
  } catch (e) { ruVoice = null; }
}

function say(text) {
  return new Promise((resolve) => {
    if (!("speechSynthesis" in window)) return resolve();
    let done = false;
    const fin = () => { if (!done) { done = true; resolve(); } };
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "ru-RU"; u.rate = 1.0;
      if (ruVoice) u.voice = ruVoice;
      u.onend = fin; u.onerror = fin;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
      setTimeout(fin, 1500 + text.length * 120);  // если onend не придёт
    } catch (e) { fin(); }
  });
}

function beep(freq = 880, ms = 180) {
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const o = actx.createOscillator(), g = actx.createGain();
    o.frequency.value = freq; g.gain.value = 0.2;
    o.connect(g); g.connect(actx.destination);
    o.start(); o.stop(actx.currentTime + ms / 1000);
  } catch (e) { /* без звука */ }
}

function zonesWith(subject, actText) {
  const zones = {};
  for (const z of ZONES) {
    const [n, act] = cfg.others[z] || [0, ""];
    const parts = n ? [`${n} ${act}`.trim()] : [];
    let count = n;
    if (subject && subject.zone === z) { count += 1; parts.push(`1 ${actText}`); }
    zones[z] = { count, activity: parts.join("; ").slice(0, 40) };
  }
  return zones;
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return r;
}

async function label(subject, actText, note, script = true) {
  const body = { zones: zonesWith(subject, actText), note: note.slice(0, 200), script };
  if (subject) body.subject = subject;
  const r = await api("/api/labels", body);
  if (!r.ok) throw new Error(`метка не записалась (HTTP ${r.status}) — запись остановлена?`);
  run.labels += 1;
}

async function ensureSession(totalSec) {
  const dv = await (await api("/api/devices")).json();
  const devs = dv.devices || [];
  const dev = devs.find((d) => d.csi_hz > 0 && d.age_s < 5) || null;
  if (!dev) throw new Error("плата не присылает CSI — проверь, что она включена");
  const r = await api("/api/sessions", { kind: "static", device_id: dev.device_id, duration_s: Math.round(totalSec + 180) });
  if (r.ok) return { sid: (await r.json()).id, mine: true };
  if (r.status === 409) {
    const act = (await (await api("/api/sessions")).json()).find((s) => !s.stopped);
    if (act) return { sid: act.id, mine: false };
  }
  throw new Error(`запись не началась (HTTP ${r.status})`);
}

function show(instr, place, stepText) {
  $("instr").textContent = instr; $("place").textContent = place || ""; $("step").textContent = stepText || "";
}

function status(text, bad = false) {
  $("status").textContent = text; $("status").classList.toggle("bad", bad);
}

async function countdown(sec, doneBefore, totalSec) {
  const t0 = performance.now(), end = t0 + sec * 1000;
  let lastTick = null;
  for (;;) {
    if (run.cancel) throw new Cancelled();
    const now = performance.now(), left = (end - now) / 1000;
    if (left <= 0) break;
    const shown = Math.ceil(left);
    $("timer").textContent = shown;
    $("bar").style.width = `${Math.min(100, (100 * (doneBefore + (now - t0) / 1000)) / totalSec)}%`;
    if (shown <= 3 && shown !== lastTick) { lastTick = shown; beep(520, 70); }  // последние 3 с — короткие тики
    await new Promise((r) => setTimeout(r, 100));
  }
}

async function keepAwake() {
  try { if (run && (!wakeLock || wakeLock.released)) wakeLock = await navigator.wakeLock?.request("screen"); } catch (e) { wakeLock = null; }
}

async function start() {
  if (run) return;
  const steps = buildPlan(true);
  if (!steps.some((s) => s.kind === "act")) return;
  const totalSec = steps.reduce((s, x) => s + x.sec, 0);
  beep(660, 60); say("Начинаем");  // в обработчике нажатия: браузер разрешает звук и речь
  run = { cancel: false, sid: null, mine: false, labels: 0 };
  $("start").disabled = true; $("stop").disabled = false; $("settings").open = false;
  await keepAwake();
  try {
    status("начинаю запись…");
    const s = await ensureSession(totalSec);
    run.sid = s.sid; run.mine = s.mine;
    status(`запись ${s.sid}${s.mine ? "" : " (уже шла)"} · не блокируй телефон`);
    await label(null, "", "сценарий: начало", false);
    let done = 0, afterMove = true, last = steps[0];
    for (let i = 0; i < steps.length; i++) {
      const st = steps[i]; last = st;
      const stepText = `шаг ${i + 1} из ${steps.length}`;
      if (st.kind === "move") {
        await label({ zone: st.zone, act: "переход", place: st.place }, `идёт ${st.place}`, `сценарий: переход → ${st.zone} ${st.place}`);
        afterMove = true;
        show(`Иди: ${st.zone}`, st.place, stepText);
        await say(`Переход. ${st.zone}, ${st.place}`);
      } else {
        if (!afterMove) await label({ zone: st.zone, act: "переход", place: st.place }, "меняет действие", "сценарий: смена действия");
        show(st.text, `${st.zone} · ${st.place}`, stepText);
        await say(st.say);
        if (run.cancel) throw new Cancelled();
        beep(880, 180);
        await label({ zone: st.zone, act: st.act, place: st.place }, `${st.label} ${st.place}`, `сценарий: ${st.zone} ${st.act} ${st.place}`);
        afterMove = false;
      }
      await countdown(st.sec, done, totalSec);
      done += st.sec;
    }
    await label({ zone: last.zone, act: "переход", place: "конец" }, "сценарий окончен", "сценарий: конец");
    beep(880, 120); setTimeout(() => beep(1175, 220), 200);
    await say("Сценарий закончен. Спасибо!");
    await finish(`готово: ${run.labels} меток в записи ${run.sid}`);
  } catch (e) {
    if (e instanceof Cancelled) {
      try { await label(null, "", "сценарий: прерван", false); } catch (_) { /* запись уже остановлена */ }
      await finish("остановлено");
    } else {
      await finish(String(e?.message || e), true);
    }
  }
}

async function finish(msg, bad = false) {
  const r = run;
  if (r?.mine && r.sid) { try { await api(`/api/sessions/${r.sid}/stop`, {}); } catch (e) { /* остановится по таймеру */ } }
  try { await wakeLock?.release(); } catch (e) { /* уже отпущен */ }
  wakeLock = null; run = null;
  show(bad ? "Ошибка" : "Готов к записи", bad ? "" : "Можно запустить ещё раз — порядок будет другим", "");
  $("timer").textContent = "—"; $("bar").style.width = "0";
  $("stop").disabled = true;
  changed();
  status(msg, bad);
}

renderSettings();
if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
$("start").onclick = start;
$("stop").onclick = () => { if (run) { run.cancel = true; try { speechSynthesis.cancel(); } catch (e) { /* нет речи */ } } };
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") keepAwake(); });
