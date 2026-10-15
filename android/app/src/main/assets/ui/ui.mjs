// СГЕНЕРИРОВАНО node v4/app/sync.mjs из v4/prod/_src/ui.mjs — не править руками
// Общие компоненты экранов «Жучок» v4 (prod). Все функции возвращают HTML-строки.
// Позиционирование экрана — абсолютное внутри 390×844; компоненты сами задают свои размеры.
import { COLORS as K, bug } from './bug.mjs';
export { bug, K };

/* ---------- размеры и кромки ---------- */
/** Размеры героя: big — только 01 Привет; mid — экраны с полем/виджетом; ring — внутри кольца */
export const HERO = { big: 340, mid: 260, ring: 224 };
/** Верх кольца ringTimer на всех экранах */
export const RING_TOP = 236;
/** Кромки «игрушка» — полупрозрачные тона палитры (CSS-токены --edgeLilac/--edgeMint/--edgeGrey/--edgeInk) */
export const EDGE = { lilac: 'rgba(122,111,240,.25)', mint: 'rgba(30,164,136,.3)', grey: 'rgba(175,167,194,.4)', ink: 'rgba(43,38,69,.55)' };

/* ---------- состояния дома: одни цвета везде ---------- */
export const STATE = {
  quiet:   { key: 'quiet',   fill: K.mint,   bg: K.mintL,  ink: K.mintT,  label: 'тихо' },
  move:    { key: 'move',    fill: K.yellow, bg: K.yellow, ink: K.tomatoT, label: 'двигается' },
  nobody:  { key: 'nobody',  fill: K.lilac,  bg: K.lilacL, ink: K.link,   label: 'никого' },
  offline: { key: 'offline', fill: K.grey,   bg: K.greyL,  ink: K.ink2,   label: 'нет связи' },
  nodata:  { key: 'nodata',  fill: K.grey,   bg: K.greyL,  ink: K.ink2,   label: 'нет данных' },
};
const HATCH_OFF = `repeating-linear-gradient(-45deg, ${K.grey} 0 5px, ${K.greyL} 5px 10px)`;
/** «нет данных» (телефон не дома) — точки; «нет связи» — штриховка; будущее — сплошной #F1EEF6 */
const DOTS = `radial-gradient(circle, ${K.grey} 1.7px, transparent 2px) 0 0/7px 7px, ${K.greyL}`;
const FILL = { offline: HATCH_OFF, nodata: DOTS };

/* ---------- иконки (24×24, stroke) ---------- */
const ic = (d, { s = 22, c = K.ink, w = 2.4, fill = 'none' } = {}) =>
  `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="${fill}" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
export const ICON = {
  back: (o) => ic('<path d="M15 5l-7 7 7 7"/>', o),
  arrow: (o) => ic('<path d="M5 12h14M13 6l6 6-6 6"/>', o),
  gear: (o) => ic('<path d="M10.3 2.8h3.4l.5 2.6 1.9.8 2.2-1.5 2.4 2.4-1.5 2.2.8 1.9 2.6.5v3.4l-2.6.5-.8 1.9 1.5 2.2-2.4 2.4-2.2-1.5-1.9.8-.5 2.6h-3.4l-.5-2.6-1.9-.8-2.2 1.5-2.4-2.4 1.5-2.2-.8-1.9-2.6-.5v-3.4l2.6-.5.8-1.9-1.5-2.2 2.4-2.4 2.2 1.5 1.9-.8z"/><circle cx="12" cy="12" r="3.2"/>', o),
  home: (o) => ic('<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/>', o),
  homeEmpty: (o) => ic('<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M10 20v-5h4v5"/>', o),
  doorIn: (o) => ic('<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"/><path d="M3 12h11M10 8l4 4-4 4"/>', o),
  move: (o) => ic('<path d="M5 7q-3 5 0 10M19 7q3 5 0 10M8.5 9.5q-1.5 2.5 0 5M15.5 9.5q1.5 2.5 0 5"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/>', o),
  wifi: (o) => ic('<path d="M3 9a13 13 0 0 1 18 0M6.5 12.5a8 8 0 0 1 11 0M10 16a3 3 0 0 1 4 0"/><circle cx="12" cy="19.5" r=".8"/>', o),
  wifiOff: (o) => ic('<path d="M3 9a13 13 0 0 1 18 0M6.5 12.5a8 8 0 0 1 11 0M10 16a3 3 0 0 1 4 0"/><path d="M4 4l16 16"/>', o),
  camOff: (o) => ic('<rect x="3" y="7" width="12" height="10" rx="3"/><path d="M15 11l5-3v8l-5-3M3 3l18 18"/>', o),
  mic: (o) => ic('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M6 11a6 6 0 0 0 12 0M12 17v4"/>', o),
  tv: (o) => ic('<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8"/>', o),
  laptop: (o) => ic('<rect x="5" y="5" width="14" height="10" rx="2"/><path d="M3 19h18"/>', o),
  speaker: (o) => ic('<rect x="6" y="3" width="12" height="18" rx="4"/><circle cx="12" cy="14" r="3"/><circle cx="12" cy="7.5" r=".8"/>', o),
  router: (o) => ic('<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 13V8M17 13V8"/><circle cx="8" cy="16.5" r=".6"/><circle cx="11" cy="16.5" r=".6"/>', o),
  socket: (o = {}) => ic(`<circle cx="12" cy="12" r="8"/><circle cx="9" cy="12" r="1.6" fill="${o.c || K.ink}" stroke="none"/><circle cx="15" cy="12" r="1.6" fill="${o.c || K.ink}" stroke="none"/>`, o),
  near: (o) => ic('<rect x="12" y="12" width="9" height="7" rx="2"/><path d="M14.5 12V8.5M18.5 12V8.5"/><path d="M3 16h6M6 13l3 3-3 3"/>', o),
  check: (o) => ic('<path d="M5 12.5l4.5 4.5L19 7.5"/>', o),
  retry: (o) => ic('<path d="M4 12a8 8 0 1 0 2.5-5.8"/><path d="M4 4v4h4"/>', o),
  phone: (o) => ic('<rect x="7" y="3" width="10" height="18" rx="3"/><path d="M11 18h2"/>', o),
};

/* ---------- страница ---------- */
export const TOKENS = `
:root{--ink:${K.ink};--ink2:${K.ink2};--bg:${K.bg};--card:${K.card};--mint:${K.mint};--mintD:${K.mintD};--mintT:${K.mintT};--mintL:${K.mintL};
--yellow:${K.yellow};--yellowD:${K.yellowD};--tomato:${K.tomato};--tomatoT:${K.tomatoT};--lilac:${K.lilac};--lilacL:${K.lilacL};--link:${K.link};
--grey:${K.grey};--greyL:${K.greyL};--err:${K.err};--soft:0 8px 24px rgba(43,38,69,.08);--edge:0 4px 0 rgba(43,38,69,.06);
--edgeLilac:${EDGE.lilac};--edgeMint:${EDGE.mint};--edgeGrey:${EDGE.grey};--edgeInk:${EDGE.ink}}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;overflow:hidden}
body{font-family:Nunito,sans-serif;font-weight:700;font-size:16px;color:var(--ink);background:var(--bg);position:relative;-webkit-font-smoothing:antialiased}
.abs{position:absolute}
.h1{font-family:Unbounded,sans-serif;font-weight:700;font-size:28px;line-height:1.12;letter-spacing:-.01em}
.h1.xl{font-size:34px}
.sub{font-size:17px;font-weight:700;color:var(--ink2);line-height:1.35}
.cap{font-size:13px;font-weight:700;color:var(--ink2)}
/* статус-бар */
.sb{position:absolute;top:0;left:0;right:0;height:48px;display:flex;align-items:center;justify-content:space-between;padding:4px 26px 0 30px;font-weight:800;font-size:15px;z-index:9}
/* шапка */
.hdr{position:absolute;top:56px;left:20px;right:20px;height:48px;display:flex;align-items:center;gap:12px}
.ibtn{flex:none;width:48px;height:48px;border-radius:16px;background:var(--card);display:grid;place-items:center;box-shadow:var(--edge),var(--soft)}
.prog{flex:1;display:flex;flex-direction:column;align-items:center;gap:7px}
.prog .cap{color:var(--ink2);font-weight:800}
.prog .seg{display:flex;gap:5px;width:100%;max-width:220px}
.prog .seg i{flex:1;height:8px;border-radius:4px;background:rgba(122,111,240,.18)}
.prog .seg i.on{background:var(--lilac)}
.prog .seg i.cur{background:var(--lilac);box-shadow:0 0 0 3px rgba(122,111,240,.22)}
/* заголовок блока */
.ttl{position:absolute;left:20px;right:20px;text-align:center}
.ttl .sub{margin-top:8px}
/* кнопки */
.btn{position:absolute;left:20px;right:20px;bottom:24px;height:60px;border-radius:28px;background:var(--yellow);color:var(--ink);
 display:flex;align-items:center;justify-content:center;gap:10px;font-weight:900;font-size:18px;box-shadow:0 6px 0 var(--yellowD),var(--soft)}
.btn.up{bottom:96px}
.btn.secondary{background:var(--lilacL);color:var(--ink);box-shadow:0 6px 0 var(--edgeLilac),var(--soft)}
.link{position:absolute;left:20px;right:20px;height:48px;display:flex;align-items:center;justify-content:center;gap:8px;font-weight:800;font-size:16px;color:var(--link)}
/* чипы */
.chip{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 14px 0 8px;border-radius:18px;background:var(--card);font-weight:800;font-size:15px;box-shadow:var(--edge);white-space:nowrap}
.chip i{width:24px;height:24px;border-radius:12px;display:grid;place-items:center;font-style:normal}
.chip.dot{padding-left:12px}.chip.dot b{width:9px;height:9px;border-radius:50%}
/* карточки */
.card{background:var(--card);border-radius:26px;box-shadow:var(--edge),var(--soft)}
.ev .row{display:flex;align-items:center;gap:12px;height:56px;padding:0 16px 0 12px}
.ev .row+.row{border-top:1.5px solid rgba(43,38,69,.07)}
.ev .row i{flex:none;width:36px;height:36px;border-radius:12px;display:grid;place-items:center}
.ev .row span{flex:1;font-weight:800;font-size:16px;white-space:nowrap}
.ev .row time{font-weight:800;font-size:15px;color:var(--ink2);font-variant-numeric:tabular-nums;white-space:nowrap}
/* шкала дня */
.day .track{position:relative;height:20px;border-radius:10px;overflow:hidden;background:var(--greyL)}
.day .track span{position:absolute;top:0;bottom:0}
.day .now{position:absolute;top:-6px;width:4px;height:32px;margin-left:-2px;border-radius:2px;background:var(--ink)}
.day .ticks{position:relative;height:18px;margin-top:6px}
.day .ticks span{position:absolute;transform:translateX(-50%);font-size:13px;font-weight:800;color:var(--ink2);font-variant-numeric:tabular-nums}
.day .ticks span:first-child{transform:none}.day .ticks span:last-child{transform:translateX(-100%)}
.day .ticks span.nowl{color:var(--ink)}
/* герой-карточка главного */
.hero{position:absolute;left:16px;right:16px;border-radius:36px;overflow:hidden}
.hero .zone{position:absolute;top:16px;left:16px}
.hero .art{position:absolute;left:50%;transform:translateX(-50%)}
.hero .st{position:absolute;left:0;right:0;bottom:24px;text-align:center}
.hero .pill{display:inline-flex;align-items:center;gap:8px;margin-top:12px;height:40px;padding:0 18px 0 14px;border-radius:20px;background:var(--card);font-weight:900;font-size:17px;box-shadow:0 4px 0 rgba(43,38,69,.08)}
/* кольцо-таймер */
.ring{position:absolute;left:50%;transform:translateX(-50%);border-radius:50%;background:var(--card);box-shadow:0 6px 0 var(--edgeLilac),var(--soft)}
.ring>svg{position:absolute;inset:0}
.ring .in{position:absolute;border-radius:50%;background:radial-gradient(circle at 50% 35%,${K.card} 0,${K.lilacL} 100%)}
.ring .art{position:absolute;left:50%;transform:translateX(-50%)}
.ring .hp{position:absolute;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:15px;display:grid;place-items:center;background:var(--card);border:2px solid var(--edgeLilac);box-shadow:0 3px 0 var(--edgeLilac)}
.ring .hp.wait{background:var(--card);border:2.5px dashed var(--grey);box-shadow:none}
.ring .hp b{position:absolute;right:-7px;top:-7px;width:20px;height:20px;border-radius:10px;background:var(--mint);display:grid;place-items:center;border:2px solid var(--card)}
.timer{position:absolute;left:50%;transform:translateX(-50%);height:52px;padding:0 22px;border-radius:26px;background:var(--ink);color:#fff;
 font-family:Unbounded,sans-serif;font-weight:700;font-size:28px;display:flex;align-items:center;font-variant-numeric:tabular-nums;box-shadow:0 5px 0 var(--edgeInk)}
/* слоты помощников */
.slots{display:flex;gap:10px;align-items:center}
.slot{position:relative;width:52px;height:52px;border-radius:18px;display:grid;place-items:center;background:var(--card);box-shadow:0 4px 0 var(--edgeLilac)}
.slot b{position:absolute;right:-6px;top:-6px;width:22px;height:22px;border-radius:11px;background:var(--mint);display:grid;place-items:center;border:2.5px solid var(--card)}
.slot.wait{background:transparent;border:2.5px dashed var(--grey);box-shadow:none}
.slots .cap{margin-left:4px}
/* стикер-звук */
.sticker{position:absolute;padding:7px 14px;border-radius:14px;font-weight:900;font-size:17px;letter-spacing:.04em;text-transform:uppercase;
 background:var(--card);box-shadow:0 4px 0 rgba(43,38,69,.1);white-space:nowrap;z-index:4}
`;

const FONTS = ''; // app: шрифты локально, @font-face в index.html

/** page({ title, body, css='', bg }) — полный HTML-документ 390×844 */
export const page = ({ title, body, css = '', bg = K.bg }) => `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=390,initial-scale=1">
<title>${title}</title>
${FONTS}
<style>${TOKENS}
body{background:${bg}}
${css}</style></head>
<body>
${body}
</body></html>
`;

/** statusBar({ time='9:41', color }) */
export const statusBar = ({ time = '9:41', color = K.ink } = {}) => `<div class="sb" style="color:${color}"><span>${time}</span>
<svg width="66" height="14" viewBox="0 0 66 14" fill="${color}" aria-hidden="true"><rect x="0" y="9" width="3.5" height="5" rx="1"/><rect x="5" y="6" width="3.5" height="8" rx="1"/><rect x="10" y="3" width="3.5" height="11" rx="1"/><rect x="15" y="0" width="3.5" height="14" rx="1"/>
<path d="M31 13.5 l-3-3.4a4.6 4.6 0 0 1 6 0z M26 8.4a7.6 7.6 0 0 1 10 0l-1.4 1.6a5.5 5.5 0 0 0-7.2 0z M24 6.2a10.6 10.6 0 0 1 14 0L36.6 7.8a8.5 8.5 0 0 0-11.2 0z"/>
<rect x="43" y="1" width="20" height="12" rx="3.5" fill="none" stroke="${color}" stroke-opacity=".4" stroke-width="1.4"/><rect x="45" y="3" width="14" height="8" rx="2"/><rect x="64" y="5" width="2" height="4" rx="1" fill-opacity=".4"/></svg></div>`;

/** taskHeader({ step, total=5, part, back=true, right='' }) — «назад» + «Задание N из 5» + сегменты.
 *  part (0..1, необязательно) — доля текущего сегмента: залито #7A6FF0, остаток rgba(122,111,240,.3).
 *  step > total приводится к total (совместимость со старой нумерацией из 7). */
export const taskHeader = ({ step, total = 5, part, back = true, right = '' }) => {
  const st = Math.min(step, total);
  const pp = part == null ? null : (Math.max(0, Math.min(1, part)) * 100).toFixed(1);
  const cur = pp == null ? '' : ` style="box-shadow:none;background:linear-gradient(90deg, ${K.lilac} ${pp}%, rgba(122,111,240,.3) ${pp}%)"`;
  return `<div class="hdr">
 ${back ? `<div class="ibtn" aria-label="Назад">${ICON.back()}</div>` : '<div style="width:48px"></div>'}
 <div class="prog"><span class="cap">Задание ${st} из ${total}</span>
  <div class="seg">${Array.from({ length: total }, (_, i) => i + 1 === st ? `<i class="cur"${cur}></i>` : `<i class="${i + 1 < st ? 'on' : ''}"></i>`).join('')}</div></div>
 ${right || '<div style="width:48px"></div>'}
</div>`;
};

/** homeHeader({ title='Мой дом', online=true, status }) — заголовок, чип связи, настройки.
 *  status (необязательно): 'online' | 'offline' | 'away' (телефон не дома); по умолчанию — из online */
const STATUS_CHIP = {
  online: () => chip({ label: 'на связи', dot: K.mint, color: K.mintT, bg: K.mintL }),
  offline: () => chip({ label: 'не на связи', dot: K.grey, color: K.ink2, bg: K.greyL }),
  away: () => chip({ label: 'телефон не дома', icon: ICON.phone({ s: 16, c: K.ink2, w: 2.6 }), iconBg: 'transparent', color: K.ink2, bg: K.greyL }),
};
export const homeHeader = ({ title = 'Мой дом', online = true, status } = {}) => `<div class="hdr" style="justify-content:space-between;align-items:flex-start;height:auto">
 <div><div class="h1" style="font-size:28px;line-height:34px">${title}</div>
  <div style="margin-top:6px">${STATUS_CHIP[status || (online ? 'online' : 'offline')]()}</div></div>
 <div class="ibtn" aria-label="Настройки">${ICON.gear()}</div></div>`;

/** title({ h, sub, top, xl }) — заголовок ≤3 слов + подзаголовок ≤6 слов, по центру */
export const title = ({ h, sub = '', top = 128, xl = false, align = 'center' }) =>
  `<div class="ttl" style="top:${top}px;text-align:${align}"><h1 class="h1${xl ? ' xl' : ''}">${h}</h1>${sub ? `<p class="sub">${sub}</p>` : ''}</div>`;

/** button({ label, arrow, up, variant }) — кнопка 60px, прибита к низу (24); up=true — поднять над ссылкой.
 *  variant: 'primary' (жёлтая, по умолчанию) | 'secondary' (#ECE9FF; только на экранах без главного действия) */
export const button = ({ label, arrow = false, up = false, variant = 'primary' }) =>
  `<div class="btn${up ? ' up' : ''}${variant === 'secondary' ? ' secondary' : ''}" role="button">${label}${arrow ? ICON.arrow({ s: 22, w: 2.8 }) : ''}</div>`;

/** textLink({ label, icon, bottom=24 }) — второстепенное действие */
export const textLink = ({ label, icon = '', bottom = 24, top }) =>
  `<div class="link" role="button" style="${top != null ? `top:${top}px` : `bottom:${bottom}px`}">${icon}${label}</div>`;

/** chip({ label, icon, iconBg, dot, color, bg }) */
export const chip = ({ label, icon = '', iconBg = K.lilacL, dot = '', color = K.ink, bg = K.card }) =>
  dot ? `<span class="chip dot" style="background:${bg};color:${color}"><b style="background:${dot}"></b>${label}</span>`
      : `<span class="chip" style="background:${bg};color:${color}">${icon ? `<i style="background:${iconBg}">${icon}</i>` : ''}${label}</span>`;

/** card({ html, style }) */
export const card = ({ html, style = '' }) => `<div class="card" style="${style}">${html}</div>`;

const EV_ICON = {
  in: { st: 'quiet', icon: 'doorIn' }, nobody: { st: 'nobody', icon: 'homeEmpty' }, move: { st: 'move', icon: 'move' },
  offline: { st: 'offline', icon: 'wifiOff' }, back: { st: 'quiet', icon: 'wifi' },
};
/** eventsCard({ rows:[{ kind:'in'|'nobody'|'move'|'offline'|'back', label, time }], style }) — одна карточка с разделителями */
export const eventsCard = ({ rows, style = '' }) => `<div class="card ev" style="${style}">${rows.map(r => {
  const e = EV_ICON[r.kind]; const s = STATE[e.st];
  const bg = e.st === 'move' ? 'rgba(255,210,63,.45)' : s.bg;
  return `<div class="row"><i style="background:${bg}">${ICON[e.icon]({ s: 20, c: e.st === 'offline' ? K.ink2 : K.ink })}</i><span>${r.label}</span><time>${r.time}</time></div>`;
}).join('')}</div>`;

/** dayScale({ segs:[[from,to,state]], now, style }) — часы 0–24. state: quiet|move|nobody|offline (штриховка)|nodata (точки).
 *  Будущее (после now) — сплошной #F1EEF6 без штриховки; маркер и время «сейчас». segs: [] — вся шкала «будущее». */
export const dayScale = ({ segs, now, style = '' }) => {
  const pc = (h) => (h / 24 * 100).toFixed(2) + '%';
  const fmt = (h) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
  const bars = segs.map(([a, b, st]) => `<span style="left:${pc(a)};width:${pc(b - a)};background:${FILL[st] || STATE[st].fill}"></span>`).join('');
  const ticks = [0, 6, 12, 18, 24].filter(h => Math.abs(h - now) > 2.6).map(h => `<span style="left:${pc(h)}">${String(h).padStart(2, '0')}</span>`);
  return `<div class="day" style="${style}"><div style="position:relative"><div class="track">${bars}</div><i class="now" style="left:${pc(now)}"></i></div>
   <div class="ticks">${ticks[0]}${ticks.slice(1, -1).join('')}<span class="nowl" style="left:${pc(now)}">${fmt(now)}</span>${ticks[ticks.length - 1]}</div></div>`;
};

/** heroCard({ state, pose, top, height, title, sub?, pill?, pillIcon?, zone='Весь дом', sticker?, bugSize, bugTop, bugOpts?, art?, extra? }) — карточка-герой главного.
 *  state: quiet | move | nobody | offline (штриховка) | nodata (точки — «нет данных», напр. телефон не дома).
 *  sub — подзаголовок под title (Nunito 17); art — свой HTML вместо жучка (позиционируется сам). */
export const heroCard = ({ state, pose, top = 128, height = 400, title: t, sub = '', pill = '', pillIcon = '', zone = 'Весь дом', sticker = null, bugSize = 196, bugTop = 52, bugOpts = {}, art = null, extra = '' }) => {
  const s = STATE[state];
  const bg = state === 'move' ? `radial-gradient(120% 80% at 50% 28%, rgba(255,253,248,.75) 0, rgba(255,253,248,0) 60%), ${K.yellow}`
    : state === 'offline' ? `repeating-linear-gradient(-45deg, rgba(175,167,194,.18) 0 6px, transparent 6px 14px), ${K.greyL}`
    : state === 'nodata' ? `radial-gradient(circle, rgba(175,167,194,.4) 1.6px, transparent 1.9px) 0 0/14px 14px, ${K.greyL}`
    : `radial-gradient(120% 80% at 50% 28%, rgba(255,253,248,.8) 0, rgba(255,253,248,0) 62%), ${s.bg}`;
  const edge = state === 'move' ? K.yellowD : state === 'nobody' ? EDGE.lilac : (state === 'offline' || state === 'nodata') ? EDGE.grey : EDGE.mint;
  return `<div class="hero" style="top:${top}px;height:${height}px;background:${bg};box-shadow:0 6px 0 ${edge},var(--soft)">
  ${zone ? `<div class="zone">${chip({ label: zone, icon: ICON.home({ s: 16, w: 2.6 }), iconBg: 'transparent' })}</div>` : ''}
  ${extra}
  ${art != null ? art : `<div class="art" style="top:${bugTop}px">${bug({ pose, size: bugSize, ...bugOpts })}</div>`}
  ${sticker ? soundSticker(sticker) : ''}
  <div class="st"><div class="h1">${t}</div>${sub ? `<p class="sub" style="margin:8px 24px 0">${sub}</p>` : ''}${pill ? `<div class="pill" style="color:${s.ink}">${pillIcon}${pill}</div>` : ''}</div>
 </div>`;
};

/** ringTimer({ top=RING_TOP, size=340, progress 0..1, content, helpers:[{icon, found, bg?}], angles }) — кольцо для долгих процессов.
 *  Помощники по умолчанию на нейтральной подложке #FFFDF8 с иконкой #2B2645. Внутрь кольца — bug размером HERO.ring. */
export const ringTimer = ({ top = RING_TOP, size = 340, progress = 0, content = '', helpers = [], angles = [] }) => {
  const c = size / 2, r = c - 22, L = 2 * Math.PI * r;
  const ticks = Array.from({ length: 12 }, (_, i) => { const a = i * Math.PI / 6; return `<circle cx="${(c + (c - 7) * Math.sin(a)).toFixed(1)}" cy="${(c - (c - 7) * Math.cos(a)).toFixed(1)}" r="${i % 3 ? 1.8 : 2.8}" fill="rgba(122,111,240,.35)"/>`; }).join('');
  const hp = helpers.map((h, i) => {
    const a = (angles[i] ?? (200 + i * 40)) * Math.PI / 180;
    const x = c + r * Math.sin(a), y = c - r * Math.cos(a);
    return h.found
      ? `<div class="hp" style="left:${x.toFixed(1)}px;top:${y.toFixed(1)}px;background:${h.bg || K.card}">${ICON[h.icon]({ s: 20 })}<b>${ICON.check({ s: 12, c: '#fff', w: 3.6 })}</b></div>`
      : `<div class="hp wait" style="left:${x.toFixed(1)}px;top:${y.toFixed(1)}px"><svg width="18" height="6" viewBox="0 0 18 6" fill="${K.grey}"><circle cx="3" cy="3" r="2.2"/><circle cx="9" cy="3" r="2.2"/><circle cx="15" cy="3" r="2.2"/></svg></div>`;
  }).join('');
  return `<div class="ring" style="top:${top}px;width:${size}px;height:${size}px">
  <svg viewBox="0 0 ${size} ${size}"><circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${K.lilacL}" stroke-width="16"/>
  <circle cx="${c}" cy="${c}" r="${r}" fill="none" stroke="${K.lilac}" stroke-width="16" stroke-linecap="round" stroke-dasharray="${(L * progress).toFixed(1)} 9999" transform="rotate(-90 ${c} ${c})"/>${ticks}</svg>
  <div class="in" style="inset:${30}px"></div>
  ${content}
  ${hp}
 </div>`;
};

/** timerPill({ top, text }) — цифры таймера */
export const timerPill = ({ top, text }) => `<div class="timer" style="top:${top}px">${text}</div>`;

/** helperSlots({ items:[{icon, bg}], searching=true, label='ищу ещё…' }) — найденные помощники + пунктирный слот */
export const helperSlots = ({ items = [], searching = true, label = 'ищу ещё…' }) => `<div class="slots">${items.map(h =>
  `<div class="slot" style="background:${h.bg || K.card}">${ICON[h.icon]({ s: 24 })}<b>${ICON.check({ s: 12, c: '#fff', w: 3.6 })}</b></div>`).join('')}
 ${searching ? `<div class="slot wait"><svg width="20" height="6" viewBox="0 0 20 6" fill="${K.grey}"><circle cx="3" cy="3" r="2.4"/><circle cx="10" cy="3" r="2.4"/><circle cx="17" cy="3" r="2.4"/></svg></div><span class="cap">${label}</span>` : ''}</div>`;

/** soundSticker({ text, x, y, rot=-6, color, bg }) — не больше одного на экран */
export const soundSticker = ({ text, x, y, rot = -6, color = K.ink, bg = K.card }) =>
  `<div class="sticker" style="left:${x}px;top:${y}px;transform:rotate(${rot}deg);color:${color};background:${bg}">${text}</div>`;
