// Прогон сценария в jsdom на виртуальных часах: какие метки и в каком порядке уходят на сервер.
// docker run --rm -v "$PWD":/w -w /w/tests_ui node:22-alpine sh -c "npm i --no-save jsdom@24.1.3 >/dev/null && node scenario.test.mjs"
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

const html = readFileSync("../ui/scenario/index.html", "utf8").replace(/<script[^>]*src=[^>]*><\/script>/, "");
const src = readFileSync("../ui/scenario/scenario.js", "utf8");
const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://t/scenario" });
const w = dom.window;

// виртуальные часы: setTimeout/performance.now управляются вручную
let now = 0, seq = 0;
const timers = [];
w.setTimeout = (fn, ms = 0) => { timers.push({ due: now + ms, fn, id: ++seq }); return seq; };
w.clearTimeout = (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); };
Object.defineProperty(w.performance, "now", { value: () => now });

const calls = [];
w.fetch = async (path, opt = {}) => {
  const body = opt.body ? JSON.parse(opt.body) : undefined;
  calls.push({ t: now, path, body });
  const json = (v, status = 200) => ({ ok: status < 400, status, json: async () => v });
  if (path === "/api/devices") return json({ devices: [{ device_id: "dev", csi_hz: 120, age_s: 0.1 }] });
  if (path === "/api/sessions") return json({ id: "test" });
  if (path === "/api/labels") return json({ session: "test" });
  if (path === "/api/sessions/test/stop") return json({ id: "test" });
  return json({}, 404);
};
w.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
w.speechSynthesis = { speak: (u) => w.setTimeout(() => u.onend?.(), 1200), cancel() {}, getVoices: () => [], onvoiceschanged: null };
w.AudioContext = class { constructor() { this.currentTime = 0; this.destination = {}; }
  createOscillator() { return { frequency: {}, connect() {}, start() {}, stop() {} }; }
  createGain() { return { gain: {}, connect() {} }; } };
Object.defineProperty(w.navigator, "wakeLock", { value: { request: async () => ({ released: false, release: async () => {} }) } });

w.eval(src);
const $ = (id) => w.document.getElementById(id);
assert.match($("total").textContent, /37 шагов/);
$("start").click();

const flush = () => new Promise((r) => setImmediate(r));
for (let guard = 0; guard < 200000; guard++) {
  await flush(); await flush();
  if (/готово|Ошибка|остановлено/.test($("status").textContent) && !$("stop").disabled === false) break;
  timers.sort((a, b) => a.due - b.due || a.id - b.id);
  const t = timers.shift();
  if (!t) break;
  now = Math.max(now, t.due);
  t.fn();
}

const labels = calls.filter((c) => c.path === "/api/labels");
assert.match($("status").textContent, /готово/, $("status").textContent);
assert.deepEqual(calls.find((c) => c.path === "/api/sessions").body.kind, "static");
assert.ok(calls.some((c) => c.path === "/api/sessions/test/stop"), "запись остановлена в конце");
assert.equal(labels[0].body.note, "сценарий: начало");
assert.equal(labels[0].body.script, false);
assert.equal(labels.at(-1).body.note, "сценарий: конец");

const acts = labels.filter((l) => l.body.subject && l.body.subject.act !== "переход");
assert.equal(acts.length, 7 * 4 + 1, "7 точек × 4 действия + покой на диване");
for (let i = 1; i < labels.length; i++) {
  const prev = labels[i - 1].body, cur = labels[i].body;
  // между двумя действиями всегда стоит метка перехода: окна смены поведения не попадут в обучение
  if (cur.subject && cur.subject.act !== "переход") assert.equal(prev.subject?.act, "переход", `перед ${cur.note}`);
}
for (const l of acts) {
  const s = l.body.subject;
  assert.ok(l.body.script === true && s.zone && s.place, l.body.note);
  const others = { "зал": 1, "спальня": 1, "комната": 2 }[s.zone] || 0;
  assert.equal(l.body.zones[s.zone].count, others + 1, `${s.zone}: в метке учтён и ведомый`);
  assert.ok(l.body.zones[s.zone].activity.length <= 40);
}
// длительность действия: от его метки до следующей метки (перехода)
const dur = { "стоит": 30, "машет": 20, "ходит": 30, "мелкие": 20, "покой": 60 };
for (const l of acts) {
  const next = labels[labels.indexOf(l) + 1];
  const sec = (next.t - l.t) / 1000;
  assert.ok(Math.abs(sec - dur[l.body.subject.act]) < 0.5, `${l.body.note}: ${sec} с`);
}
// точки перемешаны и каждая встречается ровно один раз
const order = acts.filter((l) => l.body.subject.act !== "покой").map((l) => `${l.body.subject.zone}/${l.body.subject.place}`);
assert.equal(new Set(order).size, 7);
console.log(`ok: ${labels.length} меток, ${acts.length} действий, виртуально ${(now / 60000).toFixed(1)} мин; порядок: ${[...new Set(order)].join(", ")}`);
