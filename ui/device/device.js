"use strict";
// Live device page: waterfall of CSI amplitudes + telemetry. No external deps.

const ROWS = 200;            // 10 s at 20 rows/s
const COLS = 64;
const WIFI_STATES = ["IDLE", "CONNECTING", "CONNECTED", "BSSID_NOT_FOUND", "AUTH_FAIL"];
const COUNTER_NAMES = {
  accepted: "принято", rejected_unknown: "неизвестный MAC", rejected_hmac: "неверная подпись",
  rejected_malformed: "битый пакет", rejected_replay: "повтор", callback_errors: "ошибки обработки",
};

const $ = (id) => document.getElementById(id);
const canvas = $("wf");
const ctx = canvas.getContext("2d");
const img = ctx.createImageData(COLS, ROWS);
const ring = new Float32Array(ROWS * COLS);   // row 0 = newest
let filled = 0, lo = 0, hi = 1, sinceScale = 0;
let selected = "";
const telem = {};       // device_id -> last telemetry message
let devices = [];       // from /api/devices

// Viridis-like 5-stop ramp
const STOPS = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
function color(v) {
  const x = Math.min(1, Math.max(0, v)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(x));
  const f = x - i, a = STOPS[i], b = STOPS[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

function rescale() {
  const n = Math.min(filled, ROWS) * COLS;
  if (n === 0) return;
  const vals = Array.from(ring.subarray(0, n)).sort((p, q) => p - q);
  lo = vals[Math.floor(n * 0.05)];
  hi = vals[Math.floor(n * 0.95)];
  if (hi - lo < 1e-3) hi = lo + 1;
  $("scale").textContent = `шкала: ${lo.toFixed(1)} … ${hi.toFixed(1)}`;
}

function pushRow(amp) {
  ring.copyWithin(COLS, 0, (ROWS - 1) * COLS);
  for (let c = 0; c < COLS; c++) ring[c] = amp[c] ?? 0;
  filled++;
  if (++sinceScale >= 10 || filled <= 2) { rescale(); sinceScale = 0; }
  draw();
}

function draw() {
  const span = hi - lo;
  const rows = Math.min(filled, ROWS);
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const o = (r * COLS + c) * 4;
      if (r >= rows) { img.data[o] = img.data[o + 1] = img.data[o + 2] = 0; img.data[o + 3] = 255; continue; }
      const [R, G, B] = color((ring[r * COLS + c] - lo) / span);
      img.data[o] = R; img.data[o + 1] = G; img.data[o + 2] = B; img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}

function resetWaterfall() { ring.fill(0); filled = 0; draw(); }

function fmtUptime(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} ч ${m} мин` : `${m} мин ${s % 60} с`;
}

function setText(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = "v" + (cls ? " " + cls : "");
}

function render() {
  const d = devices.find((x) => x.device_id === selected);
  const t = telem[selected] || (d && d.telemetry) || null;
  const warnings = [];
  if (d) {
    const hz = d.csi_hz;
    setText("hz_srv", hz.toFixed(1), hz >= 80 ? "okc" : "warn");
    if (hz < 80) warnings.push(`Частота CSI ${hz.toFixed(0)} Гц ниже 80 Гц.`);
    setText("rssi", d.rssi == null ? "—" : `${d.rssi} dBm`);
  }
  if (t) {
    setText("hz_dev", t.csi_hz.toFixed(1));
    const drop = t.drop_ratio * 100;
    setText("drop", `${drop.toFixed(1)} %`, t.drop_ratio > 0.2 ? "warn" : "");
    if (t.drop_ratio > 0.2) warnings.push(`Отброшено ${drop.toFixed(0)} % кадров CSI (не подходят под csi_source).`);
    const ws = WIFI_STATES[t.wifi_state] || `#${t.wifi_state}`;
    setText("wifi", ws, t.wifi_state === 2 ? "okc" : "bad");
    if (t.wifi_state !== 2) warnings.push(`Wi-Fi: ${ws}.`);
    setText("uptime", fmtUptime(t.uptime_s));
    setText("mem", `${Math.round(t.heap_free / 1024)} / ${Math.round(t.psram_free / 1024)}`);
    setText("ver", `${t.fw_ver} / ${t.model_ver}`);
  }
  $("warnings").textContent = warnings.join(" ");
}

function renderCounters(counters, unknown) {
  const tbl = $("counters");
  tbl.replaceChildren(...Object.entries(counters).map(([k, v]) => {
    const tr = document.createElement("tr");
    const a = document.createElement("td"), b = document.createElement("td");
    a.textContent = COUNTER_NAMES[k] || k;
    b.textContent = String(v);
    if (k !== "accepted" && v > 0) b.className = "warn";
    tr.append(a, b);
    return tr;
  }));
  const u = $("unknown");
  u.textContent = unknown.length ? unknown.join(", ") : "нет";
  u.className = unknown.length ? "warn" : "empty";
}

async function poll() {
  try {
    const r = await fetch("/api/devices", { cache: "no-store" });
    const body = await r.json();
    devices = body.devices;
    const sel = $("dev");
    const ids = devices.map((d) => d.device_id);
    const current = Array.from(sel.options).map((o) => o.value).filter(Boolean);
    if (ids.join() !== current.join()) {
      sel.replaceChildren(...(ids.length ? ids : [""]).map((id) => {
        const o = document.createElement("option");
        o.value = id; o.textContent = id || "— нет данных —";
        return o;
      }));
      if (!ids.includes(selected)) { selected = ids[0] || ""; resetWaterfall(); }
      sel.value = selected;
    }
    renderCounters(body.counters, body.unknown_devices);
    render();
  } catch (e) {
    // сеть недоступна — индикатор показывает WS
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  const conn = $("conn");
  ws.onopen = () => { conn.textContent = "онлайн"; conn.className = ""; };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "telem") { telem[m.device_id] = m; if (m.device_id === selected) render(); return; }
    if (m.type === "amp" && m.device_id === selected) {
      pushRow(m.amp);
      $("sig").textContent = m.sig_mode === 1 ? "кадры HT" : "кадры non-HT";
    }
  };
  ws.onclose = () => {
    conn.textContent = "нет связи с сервером"; conn.className = "bad";
    setTimeout(connect, 2000);
  };
}

$("dev").addEventListener("change", (e) => { selected = e.target.value; resetWaterfall(); render(); });
draw();
connect();
poll();
setInterval(poll, 1000);
