// «Жучок» — план дома: комнаты из обхода (метры, как их отметил телефон), жучки и точка человека.
// planSvg — рисует; редактор стен перед сохранением плана — здесь же (длина стены, перетаскивание углов,
// «выровнять углы»). Чистая геометрия без DOM — для проверки в Node.
import { K } from './ui.mjs';
import { esc } from './parts.mjs';

/* ---------------- геометрия ---------------- */
export const len = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
export const fmtM = (m) => `${(Math.round(m * 100) / 100).toFixed(2).replace('.', ',')} м`;
const centroid = (p) => [p.reduce((s, q) => s + q[0], 0) / p.length, p.reduce((s, q) => s + q[1], 0) / p.length];
const area = (p) => { let s = 0; for (let i = 0; i < p.length; i++) { const a = p[i], b = p[(i + 1) % p.length]; s += a[0] * b[1] - b[0] * a[1]; } return Math.abs(s) / 2; };

/** Стена i (угол i → угол i+1) получает длину L. Двигается «дальняя сторона» стены: все углы, что лежат за
 *  серединой стены по её направлению (у прямоугольника — противоположная стена целиком), углы не ломаются. */
export function setWallLength(poly, i, L) {
  const n = poly.length, a = poly[i], b = poly[(i + 1) % n], cur = len(a, b);
  if (!(L > 0.2) || cur < 1e-6) return poly;
  const ux = (b[0] - a[0]) / cur, uy = (b[1] - a[1]) / cur, d = L - cur;
  return poly.map((p, j) => {
    if (j === i) return p.slice();
    const proj = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy;
    return proj >= cur * 0.5 ? [p[0] + ux * d, p[1] + uy * d] : p.slice();
  });
}

/** Почти прямые углы → прямые: поворачиваем к главному направлению комнаты, стены «к осям», поворачиваем назад. */
export function orthogonalize(poly) {
  const n = poly.length;
  let sx = 0, sy = 0;
  for (let i = 0; i < n; i++) {
    const a = poly[i], b = poly[(i + 1) % n], l = len(a, b), t = Math.atan2(b[1] - a[1], b[0] - a[0]);
    sx += l * Math.cos(4 * t); sy += l * Math.sin(4 * t);
  }
  const th = Math.atan2(sy, sx) / 4, c = Math.cos(th), s = Math.sin(th);
  let q = poly.map(([x, y]) => [x * c + y * s, -x * s + y * c]);
  // косые стены (эркер, скошенный угол — больше 20° от осей) не трогаем
  const skew = q.map((a, i) => { const b = q[(i + 1) % n], t = Math.abs(Math.atan2(b[1] - a[1], b[0] - a[0])) % (Math.PI / 2);
    return Math.min(t, Math.PI / 2 - t) > Math.PI / 9; });
  for (let it = 0; it < 8; it++) {
    for (let i = 0; i < n; i++) {
      if (skew[i]) continue;
      const a = q[i], b = q[(i + 1) % n];
      if (Math.abs(b[0] - a[0]) >= Math.abs(b[1] - a[1])) { const m = (a[1] + b[1]) / 2; a[1] = m; b[1] = m; }
      else { const m = (a[0] + b[0]) / 2; a[0] = m; b[0] = m; }
    }
  }
  return q.map(([x, y]) => [x * c - y * s, x * s + y * c]);
}

export const validPoly = (p) => p.length >= 3 && area(p) >= 0.5;

/** Масштаб плана в рамку w×h: X/Y — из метров в пиксели (ось y вверх), inv — обратно. */
export function fit(rooms, w, h, pad = 16) {
  const pts = rooms.flatMap(r => r.poly);
  if (!pts.length) return null;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const s = Math.min((w - 2 * pad) / Math.max(x1 - x0, 0.5), (h - 2 * pad) / Math.max(y1 - y0, 0.5));
  const ox = (w - (x1 - x0) * s) / 2, oy = (h - (y1 - y0) * s) / 2;
  return {
    s, X: (x) => ox + (x - x0) * s, Y: (y) => oy + (y1 - y) * s,
    inv: (px, py) => [x0 + (px - ox) / s, y1 - (py - oy) / s],
  };
}

const TONE = { move: [K.yellow, K.tomato], quiet: [K.mintL, K.mint], nobody: [K.lilacL, K.lilac] };

/** plan: { rooms:[{name, poly}], boards:[[x,y]] }; dot: {x, y, r} | null; active — подсвеченная комната. */
export function planSvg({ plan, dot = null, active = '', tone = 'quiet', w = 300, h = 230, labels = true, cls = '' }) {
  const rooms = (plan?.rooms || []).filter(r => r.poly?.length >= 3);
  const f = fit(rooms, w, h);
  if (!f) return '';
  const [fill, ink] = TONE[tone] || TONE.quiet;
  const path = (p) => `M${p.map(([x, y]) => `${f.X(x).toFixed(1)} ${f.Y(y).toFixed(1)}`).join(' L')} Z`;
  const body = rooms.map(r => `<path d="${path(r.poly)}" fill="${r.name === active ? fill : K.card}" stroke="${K.ink}" stroke-width="3" stroke-linejoin="round"/>`).join('');
  // подпись — в верхнем левом углу комнаты, чтобы точка человека её не закрывала
  const names = labels ? rooms.map(r => {
    const xs = r.poly.map(p => f.X(p[0])), ys = r.poly.map(p => f.Y(p[1]));
    return `<text x="${(Math.min(...xs) + 9).toFixed(1)}" y="${(Math.min(...ys) + 18).toFixed(1)}" font-family="Nunito" font-weight="800" font-size="12" fill="${K.ink2}">${esc(r.name)}</text>`; }).join('') : '';
  const bugs = (plan?.boards || []).map(([x, y]) => `<g transform="translate(${f.X(x).toFixed(1)} ${f.Y(y).toFixed(1)})"><rect x="-7" y="-9" width="14" height="18" rx="5" fill="${K.mint}" stroke="${K.ink}" stroke-width="2"/><circle cy="-3" r="2" fill="${K.card}"/></g>`).join('');
  const d = dot ? `<g class="pd" style="transform:translate(${f.X(dot.x).toFixed(1)}px,${f.Y(dot.y).toFixed(1)}px)">
  <circle r="${Math.max(10, (dot.r || 0.6) * f.s).toFixed(1)}" fill="${ink}" opacity=".18"/>
  <circle class="pp" r="16" fill="${ink}" opacity=".35"/>
  <circle r="9" fill="${ink}" stroke="#fff" stroke-width="3"/></g>` : '';
  return `<svg class="plan ${cls}" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">${body}${names}${bugs}${d}</svg>`;
}

export const PLAN_CSS = `
.plan .pd{transition:transform 1s linear}
.plan .pp{transform-origin:0 0;animation:ping 1.6s ease-out infinite}
@keyframes ping{0%{transform:scale(.6);opacity:.5}100%{transform:scale(1.8);opacity:0}}`;

/* ---------------- редактор стен (экран walk_plan) ---------------- */
// Правки живут здесь, пока человек не нажмёт «Сохранить план»: Kotlin присылает исходный план, страница — итог.
export const edit = { src: '', rooms: [], cur: 0, sel: null, drag: null, f: null, W: 350, H: 360, fixed: {} };

export function syncEdit(plan) {
  const src = JSON.stringify(plan || []);
  if (src === edit.src) return;
  // добавили комнату — правки уже проверенных комнат (с теми же исходными углами) остаются
  const old = new Map(edit.rooms.map((r, i) => [r.name, { r, i }])), was = new Map((edit.orig || []).map(r => [r.name, JSON.stringify(r.poly)]));
  const fixed = {};
  edit.rooms = (plan || []).map((r, ni) => {
    const o = old.get(r.name);
    if (o && was.get(r.name) === JSON.stringify(r.poly)) {
      for (const [k, L] of Object.entries(edit.fixed)) { const [rr, i] = k.split(':').map(Number); if (rr === o.i) fixed[`${ni}:${i}`] = L; }
      return o.r;
    }
    return { name: r.name, poly: r.poly.map(p => p.slice()) };
  });
  edit.orig = (plan || []).map(r => ({ name: r.name, poly: r.poly }));
  edit.src = src; edit.sel = null; edit.fixed = fixed; edit.cur = Math.min(edit.cur, Math.max(0, edit.rooms.length - 1));
}

/** Редактор показывает одну комнату крупно (остальные — бледным контуром рядом): подписи стен читаются. */
export function editorSvg() {
  const { W, H } = edit;
  const cur = edit.rooms[edit.cur];
  if (!cur) return '';
  const f = edit.drag && edit.f ? edit.f : fit([cur], W, H, 52);   // пока тянем угол, масштаб не прыгает
  edit.f = f;
  const P = (p) => [f.X(p[0]), f.Y(p[1])];
  const d = (pts) => `M${pts.map(p => p.map(v => v.toFixed(1)).join(' ')).join(' L')} Z`;
  let out = edit.rooms.map((r, ri) => ri === edit.cur ? '' :
    `<path d="${d(r.poly.map(P))}" fill="none" stroke="${K.grey}" stroke-width="2" stroke-dasharray="6 6" data-ui="planRoom" data-r="${ri}"/>`).join('');
  const ri = edit.cur, pts = cur.poly.map(P);
  const [cx, cy] = P(centroid(cur.poly));
  out += `<path d="${d(pts)}" fill="${K.card}" stroke="${K.ink}" stroke-width="3.5" stroke-linejoin="round"/>`;
  pts.forEach((a, i) => {
    const b = pts[(i + 1) % pts.length], on = edit.sel && edit.sel.r === ri && edit.sel.i === i;
    out += `<line x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}" stroke="${on ? K.yellow : 'transparent'}" stroke-width="${on ? 9 : 26}" stroke-linecap="round" data-ui="planWall" data-r="${ri}" data-i="${i}"/>`;
  });
  pts.forEach((a, i) => {
    const b = pts[(i + 1) % pts.length], on = edit.sel && edit.sel.r === ri && edit.sel.i === i;
    let mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2; const dl = Math.hypot(cx - mx, cy - my) || 1, k = Math.min(26, dl * 0.4) / dl;
    mx += (cx - mx) * k; my += (cy - my) * k;
    const t = fmtM(len(cur.poly[i], cur.poly[(i + 1) % cur.poly.length]));
    out += `<g data-ui="planWall" data-r="${ri}" data-i="${i}" transform="translate(${mx.toFixed(1)} ${my.toFixed(1)})"><rect x="-34" y="-15" width="68" height="30" rx="15" fill="${on ? K.yellow : K.lilacL}" stroke="${on ? K.yellowD : 'transparent'}" stroke-width="2"/>
<text text-anchor="middle" dominant-baseline="middle" y="1" font-family="Nunito" font-weight="900" font-size="14" fill="${K.ink}">${t}</text></g>`;
  });
  pts.forEach((a, i) => { out += `<circle cx="${a[0].toFixed(1)}" cy="${a[1].toFixed(1)}" r="11" fill="${K.lilac}" stroke="#fff" stroke-width="3.5" data-corner="1" data-r="${ri}" data-i="${i}"/>`; });
  return `<svg class="ed" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="touch-action:none;overflow:hidden">${out}</svg>`;
}

/** Действия редактора (их вызывает app.js): true — нужно перерисовать. */
export const PLAN = {
  click(el) {
    const k = el.dataset.ui;
    if (k === 'planRoom') { edit.cur = +el.dataset.r; edit.sel = null; return true; }
    if (k === 'planWall') { const r = +el.dataset.r, i = +el.dataset.i; edit.sel = edit.sel && edit.sel.r === r && edit.sel.i === i ? null : { r, i }; return true; }
    if (k === 'planMinus' || k === 'planPlus') return this.nudge(k === 'planPlus' ? 0.05 : -0.05);
    if (k === 'planOrtho') {
      const rs = edit.sel ? [edit.sel.r] : edit.rooms.map((_, i) => i);
      for (const r of rs) {
        edit.rooms[r].poly = orthogonalize(edit.rooms[r].poly);
        // длины, которые человек ввёл сам, после выравнивания возвращаем точно
        for (const [k, L] of Object.entries(edit.fixed)) { const [rr, i] = k.split(':').map(Number); if (rr === r) edit.rooms[r].poly = setWallLength(edit.rooms[r].poly, i, L); }
      }
      return true;
    }
    if (k === 'planReset') {   // «Как было»: выбранная комната (или все) — как её отметили углами
      const rs = edit.sel ? [edit.sel.r] : [edit.cur];
      for (const r of rs) { const o = (edit.orig || [])[r]; if (o) edit.rooms[r] = { name: o.name, poly: o.poly.map(p => p.slice()) };
        for (const k of Object.keys(edit.fixed)) if (+k.split(':')[0] === r) delete edit.fixed[k]; }
      edit.sel = null;
      return true;
    }
    if (k === 'planDone') { edit.sel = null; return true; }
    return false;
  },
  nudge(dm) {
    if (!edit.sel) return false;
    const r = edit.rooms[edit.sel.r], p = r.poly, i = edit.sel.i;
    const L = len(p[i], p[(i + 1) % p.length]) + dm;
    r.poly = setWallLength(p, i, L); edit.fixed[`${edit.sel.r}:${i}`] = L;
    return true;
  },
  setLen(v) {
    if (!edit.sel) return false;
    const L = parseFloat(String(v).replace(',', '.'));
    if (!(L >= 0.3 && L <= 30)) return false;
    const r = edit.rooms[edit.sel.r];
    r.poly = setWallLength(r.poly, edit.sel.i, L); edit.fixed[`${edit.sel.r}:${edit.sel.i}`] = L;
    return true;
  },
  down(e) {
    const c = e.target.closest && e.target.closest('[data-corner]');
    if (!c || !edit.f) return false;
    const fld = document.getElementById('wallLen');      // введённая длина применяется до того, как тянут угол
    if (fld && document.activeElement === fld) { this.setLen(fld.value); fld.blur(); }
    const svg = c.ownerSVGElement;
    edit.drag = { r: +c.dataset.r, i: +c.dataset.i, svg };
    try { svg.setPointerCapture(e.pointerId); } catch {}
    return true;
  },
  move(e) {
    if (!edit.drag) return false;
    const rect = edit.drag.svg.getBoundingClientRect();
    const sx = edit.W / rect.width, sy = edit.H / rect.height;
    const p = edit.f.inv((e.clientX - rect.left) * sx, (e.clientY - rect.top) * sy);
    const { r, i } = edit.drag, n = edit.rooms[r].poly.length;
    edit.rooms[r].poly[i] = p;
    delete edit.fixed[`${r}:${i}`]; delete edit.fixed[`${r}:${(i + n - 1) % n}`];   // угол сдвинули руками — эти длины больше не «введённые»
    return true;
  },
  up() { const d = !!edit.drag; edit.drag = null; return d; },
  result() { return edit.rooms.map(r => ({ name: r.name, poly: r.poly.map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]) })); },
  bad() { return edit.rooms.filter(r => !validPoly(r.poly)).map(r => r.name); },
};
