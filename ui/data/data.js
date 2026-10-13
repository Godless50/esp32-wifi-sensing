// Список записей и их метки (только просмотр).
const KIND = { walk: "обход", static: "запись", empty: "пустая квартира" };
const list = document.getElementById("list");

const two = (n) => String(n).padStart(2, "0");
const mmss = (s) => `${two(Math.floor(s / 60))}:${two(Math.floor(s % 60))}`;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

async function labels(s, box) {
  box.replaceChildren(el("p", "muted", "загрузка меток…"));
  try {
    const r = await fetch(`/api/sessions/${encodeURIComponent(s.id)}/labels`, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const ls = await r.json();
    if (!ls.length) { box.replaceChildren(el("p", "muted", "  меток нет")); return; }
    const ol = el("ol");
    for (const l of ls) {
      const li = el("li");
      li.append(el("span", "t", mmss(Math.max(0, l.t))));
      const who = Object.entries(l.zones || {}).filter(([, v]) => v.count > 0).map(([z, v]) => `${z}: ${v.count} ${v.activity}`.trim());
      li.append(`${l.note || "—"}${who.length ? " · " + who.join("; ") : ""}`);
      ol.append(li);
    }
    box.replaceChildren(ol);
  } catch (e) {
    box.replaceChildren(el("p", "muted", `не удалось загрузить метки: ${e.message}`));
  }
}

async function load() {
  try {
    const r = await fetch("/api/sessions", { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const ss = (await r.json()).sort((a, b) => b.started_wall - a.started_wall);
    list.replaceChildren();
    if (!ss.length) { list.append(el("p", "muted", "Записей пока нет.")); return; }
    for (const s of ss) {
      const d = new Date(s.started_wall * 1000);
      const dur = s.stopped ? s.stopped - s.started : null;
      const det = el("details", "s");
      const sum = el("summary");
      sum.append(el("span", "when", `${two(d.getDate())}.${two(d.getMonth() + 1)} ${two(d.getHours())}:${two(d.getMinutes())}`));
      sum.append(el("span", "tag", KIND[s.kind] || s.kind));
      if (!s.stopped) sum.append(el("span", "tag live", "идёт"));
      sum.append(el("span", "muted", `${dur === null ? "…" : mmss(dur)} · ${s.csi_packets} пакетов CSI${s.poses ? ` · ${s.poses} поз` : ""}`));
      sum.append(el("span", "muted", s.id));
      const box = el("div");
      det.append(sum, box);
      det.addEventListener("toggle", () => { if (det.open) labels(s, box); });
      list.append(det);
    }
  } catch (e) {
    document.getElementById("err").textContent = `Не удалось загрузить список: ${e.message}`;
    list.replaceChildren();
  }
}

load();
