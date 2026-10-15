// Части экранов, перенесённые из v4/prod/_src/screens/*.mjs (без page()/statusBar).
// Источник указан у каждого блока; при изменении макетов переносить сюда же.
import { ICON, K, EDGE, STATE, bug, HERO, chip } from './ui.mjs';

const ic = (d, { s = 22, c = K.ink, w = 2.4 } = {}) =>
  `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;

/* ---------- 02-cable: пиктограммы, поле, «штекер с замком», телефон ---------- */
export const PIC = {
  lock: (o) => ic('<rect x="5" y="10.5" width="14" height="10" rx="3"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/><path d="M12 14.5v2"/>', o),
  plug: (o) => ic('<path d="M9 3v4M15 3v4"/><rect x="6" y="7" width="12" height="8" rx="3"/><path d="M12 15v3q0 3-4 3"/>', o),
  chip: (o) => ic('<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3"/><path d="M10 12h4"/>', o),
  chevron: (o) => ic('<path d="M9 5l7 7-7 7"/>', o),
  cupboard: (o) => ic('<rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M12 3v18M9.5 11v2M14.5 11v2"/>', o),
  bell: (o) => ic('<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>', o),
  battery: (o) => ic('<rect x="3" y="7" width="16" height="10" rx="3"/><path d="M21 10.5v3"/><path d="M7 10.5v3M10.5 10.5v3"/>', o),
  trash: (o) => ic('<path d="M4 7h16M10 3.5h4M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.5h6a1.5 1.5 0 0 0 1.5-1.5l1-12.5M10 11v6M14 11v6"/>', o),
  chevR: (o) => ic('<path d="M9.5 5.5l6.5 6.5-6.5 6.5"/>', o),
  up: (o) => ic('<path d="M5.5 15l6.5-6.5 6.5 6.5"/>', o),
  plus: (o) => ic('<path d="M12 5v14M5 12h14"/>', o),
  wifiKey: (o) => ic('<path d="M2.5 9a13 13 0 0 1 17 -1.2M6 12.5a8 8 0 0 1 9-1.4M9.5 16a3 3 0 0 1 2.6-.9"/><circle cx="17" cy="16" r="2.6"/><path d="M17 18.6V22M17 20.4h1.8"/>', o),
  route: (o) => ic('<circle cx="6" cy="18" r="2.4"/><path d="M8.4 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/><path d="M15.5 3.5l3 2.5-3 2.5"/>', o),
  cam: (o) => ic('<rect x="3" y="7" width="18" height="13" rx="3.5"/><path d="M8.5 7l1.5-3h4l1.5 3"/><circle cx="12" cy="13.5" r="3.4"/>', o),
  clock: (o) => ic('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>', o),
};

export const plugLock = ({ s = 40 } = {}) => `<span class="plk" style="width:${s}px;height:${s}px">${PIC.plug({ s: Math.round(s * .55), w: 2.6 })}<b>${PIC.lock({ s: 12, c: '#fff', w: 3 })}</b></span>`;

export const SETUP_CSS = `
.plk{position:relative;flex:none;display:grid;place-items:center;border-radius:14px;background:var(--mintL)}
.plk b{position:absolute;right:-6px;top:-6px;width:22px;height:22px;border-radius:11px;background:var(--lilac);display:grid;place-items:center;border:2.5px solid var(--card)}
.keep{display:flex;align-items:center;gap:12px;height:56px;padding:0 20px 0 10px;border-radius:28px;background:var(--card);box-shadow:var(--edge);font-weight:800;font-size:15px;color:var(--ink);white-space:nowrap}
.cv .keep{position:absolute;left:50%;transform:translateX(-50%)}
.bot .keep{align-self:center}
.fld{position:absolute;left:20px;right:20px;height:68px;border-radius:24px;background:var(--card);box-shadow:var(--edge),var(--soft);display:flex;align-items:center;gap:12px;padding:0 6px 0 12px}
.fld>i{flex:none;width:40px;height:40px;border-radius:14px;display:grid;place-items:center}
.fld .v{flex:1;min-width:0;display:flex;flex-direction:column;gap:1px}
.fld .v small{font-size:13px;font-weight:800;color:var(--ink2);display:flex;align-items:center;gap:6px}
.fld input{all:unset;box-sizing:border-box;width:100%;font:800 17px/24px Nunito,sans-serif;color:var(--ink);caret-color:var(--lilac)}
.fld input[type=password]{letter-spacing:.12em;font-size:20px}
.fld input::placeholder{color:var(--grey);letter-spacing:0;font-size:17px}
.fld .show{flex:none;height:48px;padding:0 12px;border-radius:16px;display:flex;align-items:center;font-weight:800;font-size:15px;color:var(--link)}
.fld:focus-within{box-shadow:0 0 0 3px var(--lilac),0 0 0 8px rgba(122,111,240,.18),var(--soft)}
.fld .chev{flex:none;width:48px;height:48px;border-radius:16px;display:grid;place-items:center}
.only{display:inline-flex;white-space:nowrap;align-items:center;gap:4px;height:22px;padding:0 9px 0 6px;border-radius:11px;background:var(--lilacL);color:var(--link);font-size:13px;font-weight:800}`;

/** поле ввода (настоящий <input>); id стабилен — поле не пересоздаётся при обновлениях */
export const inputField = ({ top, id, icon, iconBg = K.lilacL, label, type = 'text', value = '', placeholder = '', show = false, extra = '' }) =>
  `<label class="fld" style="top:${top}px"><i style="background:${iconBg}">${icon}</i>
  <span class="v"><small>${label}</small><input id="${id}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off" autocapitalize="off" spellcheck="false"${type === 'password' ? ' enterkeyhint="go"' : ''}></span>
  ${show ? `<span class="show" role="button" data-ui="togglePw">${show === 'on' ? 'скрыть' : 'показать'}</span>` : ''}${extra}</label>`;

export const onlyBug = () => `<span class="only" aria-label="Пароль уходит только в жучок">${PIC.lock({ s: 13, c: K.link, w: 3 })}только жучку</span>`;

export const phoneScene = ({ bx, by, ask = false }) => {
  const k = HERO.mid / 272, px = bx + 232 * k, port = by + 68 * k - (ask ? 24 : 9);
  return `<svg class="abs" style="left:${(px - 37.6).toFixed(1)}px;top:${(port - 105.8).toFixed(1)}px" width="96" height="124" viewBox="0 0 96 124" aria-hidden="true">
 <g transform="rotate(14 48 60)">
  <rect x="20" y="8" width="58" height="100" rx="14" fill="${K.ink}"/>
  <rect x="25" y="14" width="48" height="84" rx="9" fill="${K.card}"/>
  <circle cx="49" cy="46" r="17" fill="${K.lilacL}"/>
  <g transform="translate(33 30)">${bug({ pose: 'wave', size: 32 })}</g>
  <rect x="35" y="72" width="28" height="6" rx="3" fill="${EDGE.lilac}"/><rect x="39" y="83" width="20" height="6" rx="3" fill="${EDGE.lilac}"/>
  <rect x="40" y="104" width="18" height="5" rx="2.5" fill="${K.grey}"/>
 </g></svg>
${ask ? `<div class="ask" style="left:${(px + 14).toFixed(1)}px;top:${(port - 4).toFixed(1)}px">?</div>`
  : `<svg class="abs" style="left:${(px - 32).toFixed(1)}px;top:${(port - 14).toFixed(1)}px" width="64" height="30" viewBox="0 0 64 30" aria-hidden="true"><g stroke="${K.lilac}" stroke-width="3.5" stroke-linecap="round"><path d="M18 24 L8 20"/><path d="M20 15 L12 9"/><path d="M46 24 L56 20"/><path d="M44 15 L52 9"/></g></svg>`}`;
};
export const ASK_CSS = `.ask{position:absolute;width:34px;height:34px;border-radius:17px;background:var(--lilac);color:#fff;display:grid;place-items:center;font-weight:900;font-size:20px;border:3px solid var(--card);box-shadow:0 3px 0 var(--edgeLilac)}`;

/* ---------- 03-recruit: этапы ---------- */
export const stages = (items, top) => `<div class="stg" style="top:${top}px">${items.map((s, i) => `${i ? `<span class="ln${s.state !== 'wait' ? ' on' : ''}"></span>` : ''}
  <div class="st ${s.state}"><i>${s.icon}${s.state === 'done' ? `<b>${ICON.check({ s: 12, c: '#fff', w: 3.6 })}</b>` : ''}</i><small>${s.label}</small></div>`).join('')}</div>`;
export const STAGES_CSS = `
.stg{position:absolute;left:20px;right:20px;display:flex;align-items:flex-start;justify-content:center}
.stg .st{width:84px;display:flex;flex-direction:column;align-items:center;gap:8px}
.stg .st i{position:relative;width:52px;height:52px;border-radius:18px;display:grid;place-items:center;font-style:normal;box-shadow:var(--edge)}
.stg .st.done i{background:var(--mintL)}
.stg .st.cur i{background:var(--card);box-shadow:0 0 0 3px var(--lilac),0 0 0 8px rgba(122,111,240,.18)}
.stg .st.wait i{background:transparent;border:2.5px dashed var(--grey);box-shadow:none}
.stg .st i b{position:absolute;right:-6px;top:-6px;width:22px;height:22px;border-radius:11px;background:var(--mint);display:grid;place-items:center;border:2.5px solid var(--card)}
.stg .st small{font-size:13px;font-weight:800;color:var(--ink2)}
.stg .st.cur small{color:var(--link)}
.stg .ln{flex:none;width:28px;height:4px;margin-top:24px;border-radius:2px;background:repeating-linear-gradient(90deg,var(--grey) 0 4px,transparent 4px 8px)}
.stg .ln.on{background:var(--lilac)}`;

/* ---------- 05-place: карточки да/нет, мини-кольцо ---------- */
const mini = (x, y, s = 44, o = {}) => `<g transform="translate(${x} ${y})">${bug({ pose: 'listen', size: s, ...o })}</g>`;
const SCENE = {
  shelf: () => `${mini(22, 2, 52)}<rect x="10" y="52" width="76" height="8" rx="3" fill="${K.yellow}"/><rect x="10" y="57" width="76" height="3" rx="1.5" fill="${K.yellowD}"/>
    <path d="M22 60 L22 70 L32 60Z M74 60 L74 70 L64 60Z" fill="${K.yellowD}"/>`,
  cupboard: () => `<rect x="22" y="2" width="52" height="68" rx="6" fill="${K.lilac}"/><rect x="27" y="7" width="42" height="58" rx="3" fill="${K.ink}"/>
    ${mini(26, 20, 44, { eyes: 'half', mouth: 'flat', ant: 'droop' })}
    <path d="M27 7 L14 13 L14 66 L27 65Z" fill="${K.lilacL}"/><circle cx="18" cy="38" r="2.2" fill="${K.lilac}"/>`,
  router: () => `${mini(0, 4, 52, { eyes: 'wide', mouth: 'o', ant: 'kink', look: [3, 0] })}
    <rect x="48" y="42" width="40" height="18" rx="5" fill="${K.ink}"/><path d="M56 42 V24 M80 42 V24" stroke="${K.ink}" stroke-width="4" stroke-linecap="round"/>
    <circle cx="57" cy="51" r="2" fill="${K.mint}"/><circle cx="64" cy="51" r="2" fill="${K.mint}"/>
    <g fill="none" stroke="${K.grey}" stroke-width="2.6" stroke-linecap="round"><path d="M70 22 Q76 16 82 22"/><path d="M66 16 Q76 6 86 16"/></g>
    <rect x="4" y="60" width="86" height="4" rx="2" fill="${K.grey}" opacity=".5"/>`,
};
export const yesNo = (items, top) => `<div class="yn" style="top:${top}px">${items.map((c, i) => `<div class="ync ${c.ok ? 'ok' : 'no'}" style="transform:rotate(${[-3, 2, -2][i] || 0}deg)">
  <svg width="96" height="72" viewBox="0 0 96 72" aria-hidden="true">${SCENE[c.scene]()}</svg>
  <b>${c.ok ? ICON.check({ s: 15, c: '#fff', w: 3.6 }) : `<svg width="13" height="13" viewBox="0 0 24 24" stroke="#fff" stroke-width="4.4" stroke-linecap="round"><path d="M5 5l14 14M19 5L5 19"/></svg>`}</b>
  <span>${c.label}</span></div>`).join('')}</div>`;
export const YN_CSS = `
.yn{position:absolute;left:20px;right:20px;display:flex;gap:10px}
.ync{position:relative;flex:1;height:132px;border-radius:24px;display:flex;flex-direction:column;align-items:center;padding-top:14px;gap:6px}
.ync.ok{background:var(--mintL);box-shadow:0 5px 0 var(--edgeMint),var(--soft)}
.ync.no{background:var(--greyL);box-shadow:0 5px 0 var(--edgeGrey),var(--soft)}
.ync>b{position:absolute;right:-6px;top:-8px;width:30px;height:30px;border-radius:15px;display:grid;place-items:center;border:3px solid var(--card)}
.ync.ok>b{background:var(--mint)}.ync.no>b{background:var(--ink)}
.ync span{font-size:15px;font-weight:800;line-height:1.15;text-align:center}
.ync.no span{color:var(--ink2)}`;
export const miniRing = () => `<svg class="spin" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><circle cx="14" cy="14" r="10.5" fill="none" stroke="${EDGE.lilac}" stroke-width="5"/>
 <circle cx="14" cy="14" r="10.5" fill="none" stroke="${K.lilac}" stroke-width="5" stroke-linecap="round" stroke-dasharray="20 999" transform="rotate(-90 14 14)"/></svg>`;

/* ---------- 13-offline: «Что проверить» ---------- */
export const CHECKS = [[ICON.socket, 'В розетке?'], [ICON.router, 'Роутер работает?'], [PIC.cupboard, 'Не в шкафу?'], [ICON.near, 'Поближе к роутеру?']];
export const CHECK_CSS = `.ck{position:absolute;left:20px;right:20px}.ck .row{height:48px}.ck .row i{background:var(--greyL)}`;
export const checkList = ({ top }) => `<div class="card ev ck" style="top:${top}px">${CHECKS.map(([f, l]) => `<div class="row"><i>${f({ s: 20 })}</i><span>${l}</span></div>`).join('')}</div>`;

/* ---------- 07-walk-intro: маршрут ---------- */
export const PICTO = {
  door: `<rect x="12" y="6" width="30" height="54" rx="3" fill="${K.lilac}"/><rect x="16" y="10" width="22" height="50" rx="2" fill="${K.ink}"/>
    <path d="M38 10 L54 4 L54 63 L38 60 Z" fill="${K.card}" stroke="${K.lilacL}" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="49" cy="34" r="3.6" fill="${K.yellow}"/><ellipse cx="32" cy="61" rx="24" ry="2.6" fill="${K.ink}" opacity=".12"/>`,
  corner: `<path d="M32 8 L32 44 L4 56 L4 14 Z" fill="${K.lilacL}"/><path d="M32 8 L32 44 L60 56 L60 14 Z" fill="${K.greyL}"/>
    <path d="M4 56 L32 44 L60 56 L60 62 L4 62 Z" fill="${K.bg}"/>
    <path d="M32 8 L32 44 M32 44 L4 56 M32 44 L60 56" stroke="${K.ink}" stroke-opacity=".25" stroke-width="2" fill="none"/>
    <ellipse cx="32" cy="44" rx="6" ry="2.2" fill="${K.ink}" opacity=".3"/>
    <path d="M32 44 L32 28" stroke="${K.ink}" stroke-width="3.5" stroke-linecap="round"/>
    <circle cx="32" cy="22" r="8" fill="${K.yellow}"/><circle cx="32" cy="24.5" r="5.5" fill="${K.yellowD}" opacity=".35"/><circle cx="29.3" cy="19.3" r="2.2" fill="#fff" opacity=".9"/>`,
  rooms: `<rect x="5" y="8" width="54" height="48" rx="8" fill="${K.card}"/>
    <rect x="9" y="12" width="21" height="22" rx="3" fill="${K.lilacL}"/><rect x="34" y="12" width="21" height="22" rx="3" fill="${K.mintL}"/>
    <rect x="9" y="38" width="46" height="14" rx="3" fill="rgba(255,210,63,.45)"/>
    <rect x="5" y="8" width="54" height="48" rx="8" fill="none" stroke="${K.ink}" stroke-width="3.5"/>
    <path d="M32 8 V22 M32 30 V36 M5 36 H18 M26 36 H40 M48 36 H59" stroke="${K.ink}" stroke-width="3.5" stroke-linecap="round"/>
    <path d="M19 22 Q22 45 32 45 Q44 45 44 22" fill="none" stroke="${K.yellowD}" stroke-width="3.2" stroke-linecap="round" stroke-dasharray="0.5 6"/>
    <circle cx="44" cy="22" r="3.6" fill="${K.ink}"/>`,
};
const P = (k, s = 64) => `<svg width="${s}" height="${s}" viewBox="0 0 64 64" aria-hidden="true">${PICTO[k]}</svg>`;
export const STEPS = [['door', 'За дверь', '2 мин'], ['corner', 'Углы', ''], ['rooms', 'Комнаты', ''], ['door', 'За дверь', '2 мин']];
export const arrow = (rot, s = 30) => `<svg class="ar" width="${s}" height="${s}" viewBox="0 0 24 24" style="transform:rotate(${rot}deg)" fill="none" stroke="${K.lilac}" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"/></svg>`;
export const routeRow = (top) => `<div class="rr" style="top:${top}px">${STEPS.map(([k], i) =>
  `${i ? arrow(0, 22) : ''}<span class="rt">${P(k, 42)}<b>${i + 1}</b></span>`).join('')}</div>`;
export const ROUTE_ROW_CSS = `
.rr{position:absolute;left:20px;right:20px;display:flex;align-items:center;justify-content:center;gap:4px}
.rr .ar{position:static}
.rt{position:relative;width:64px;height:64px;border-radius:20px;background:var(--card);display:grid;place-items:center;box-shadow:0 4px 0 ${EDGE.lilac}}
.rt b{position:absolute;left:-6px;top:-6px;width:22px;height:22px;border-radius:11px;display:grid;place-items:center;font-size:13px;font-weight:900;color:var(--ink);background:radial-gradient(circle at 35% 30%,${K.yellow} 20%,${K.yellowD} 100%)}`;
export const walkTile = ([k, cap, sub], i) => `<div class="tl t${i + 1}">${P(k)}<span>${cap}${sub ? ` <em>· ${sub}</em>` : ''}</span><b>${i + 1}</b></div>`;
export const WALK_INTRO_CSS = `.dur{position:absolute;top:168px;left:0;right:0;display:flex;justify-content:center}
.board{position:absolute;left:50%;margin-left:-175px;width:350px;top:228px;height:252px}
.tl{position:absolute;width:150px;height:108px;border-radius:24px;background:var(--card);box-shadow:0 5px 0 var(--edgeLilac),var(--soft);
 display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}
.tl span{font-size:15px;font-weight:800;white-space:nowrap}.tl em{font-style:normal;color:var(--ink2)}
.tl>b{position:absolute;left:-6px;top:-8px;width:28px;height:28px;border-radius:14px;display:grid;place-items:center;font-size:15px;font-weight:900;
 background:radial-gradient(circle at 35% 30%,${K.yellow} 20%,${K.yellowD} 100%);box-shadow:0 2px 0 rgba(43,38,69,.12)}
.t1{left:0;top:0;transform:rotate(-2deg)}.t2{right:0;top:0;transform:rotate(2deg)}.t3{right:0;bottom:0;transform:rotate(-1.5deg)}.t4{left:0;bottom:0;transform:rotate(1.5deg)}
.board .ar{position:absolute}
.tok{position:absolute;left:50%;top:50%;transform:translate(-50%,-56%)}
.rms{position:absolute;left:20px;right:20px;top:500px;display:flex;flex-wrap:wrap;justify-content:center;column-gap:8px}
.rms .hit{height:48px;display:flex;align-items:center}
.rms .chip{box-shadow:var(--edge)}`;

/* ---------- 07a-walk-mark: чип «видео не пишу» ---------- */
export const recOffChip = () => `<span class="chip rec"><i class="recb"><b></b>REC<s></s></i>видео не пишу</span>`;
export const REC_CSS = `.chip.rec{gap:8px;padding-left:6px}
.recb{position:relative;width:auto!important;height:24px;padding:0 7px 0 6px;border-radius:12px!important;background:${K.greyL};display:flex!important;align-items:center;gap:4px;font-style:normal;font-size:13px;font-weight:900;color:${K.ink2};letter-spacing:.02em}
.recb b{width:9px;height:9px;border-radius:50%;background:${K.tomato}}
.recb s{position:absolute;left:3px;right:3px;top:11px;height:2px;border-radius:1px;background:${K.ink};transform:rotate(-12deg)}`;

/* ---------- 10-result: умения, конфетти ---------- */
export const ROOMS_ICON = (o) => ic('<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M12 11v9M4 15h8"/>', o);
export const skills = ({ rooms = false, presence = true, motion = true, style = '' }) => {
  const done = `<b class="mk">${ICON.check({ s: 16, c: '#fff', w: 3.6 })}</b>`;
  const wait = `<b class="mk no"></b>`;
  const rows = [
    { icon: ICON.home({ s: 20 }), bg: `linear-gradient(135deg, ${K.mintL} 50%, ${K.lilacL} 50%)`, label: 'Кто-то дома / никого', mk: presence ? done : wait },
    { icon: ICON.move({ s: 20 }), bg: 'rgba(255,210,63,.45)', label: 'Движение', mk: motion ? done : wait },
    rooms
      ? { icon: ROOMS_ICON({ s: 20 }), bg: K.mintL, label: 'Комнаты', cap: rooms, mk: done }
      : { icon: ROOMS_ICON({ s: 20, c: K.ink2 }), bg: 'transparent', dashed: true, label: 'Комнаты', cap: 'пока весь дом одной зоной', mk: wait },
  ];
  return `<div class="card sk" style="${style}">${rows.map(r =>
    `<div class="row"><i style="background:${r.bg}"${r.dashed ? ' class="dash"' : ''}>${r.icon}</i><span>${r.label}${r.cap ? `<small>${r.cap}</small>` : ''}</span>${r.mk}</div>`).join('')}</div>`;
};
export const SK_CSS = `
.sk .row{display:flex;align-items:center;gap:12px;min-height:60px;padding:8px 16px 8px 12px}
.sk .row+.row{border-top:1.5px solid rgba(43,38,69,.07)}
.sk .row i{flex:none;width:40px;height:40px;border-radius:13px;display:grid;place-items:center}
.sk .row i.dash{border:2.5px dashed var(--grey)}
.sk .row span{flex:1;font-weight:800;font-size:17px;line-height:1.2}
.sk .row small{display:block;font-size:14px;font-weight:700;color:var(--ink2);margin-top:2px}
.sk .mk{flex:none;width:30px;height:30px;border-radius:15px;background:var(--mint);display:grid;place-items:center;box-shadow:0 3px 0 var(--edgeMint)}
.sk .mk.no{background:transparent;border:2.5px dashed var(--grey);box-shadow:none}`;
export const CONFETTI = `<svg class="deco" width="390" height="844" aria-hidden="true">
 <circle cx="46" cy="246" r="7" fill="${K.mint}"/><rect x="330" y="232" width="16" height="16" rx="5" fill="${K.yellow}" transform="rotate(20 338 240)"/>
 <rect x="28" y="404" width="18" height="10" rx="5" fill="${K.blush}" transform="rotate(-30 37 409)"/><circle cx="354" cy="332" r="6" fill="${K.tomato}"/>
 <rect x="80" y="214" width="12" height="7" rx="3.5" fill="${K.lilac}" transform="rotate(35 86 217)"/></svg>`;

/* ---------- 12-home-rooms: дом в разрезе — обобщён на 2–6 комнат из state ---------- */
const SH = (a) => `rgba(43,38,69,${a})`;
const FURN = {
  bed: (r) => `<g>
    <rect x="${r.x + 10}" y="${r.y + 22}" width="9" height="36" rx="4" fill="${SH(.38)}"/>
    <rect x="${r.x + 12}" y="${r.y + 38}" width="80" height="14" rx="5" fill="${SH(.28)}"/>
    <rect x="${r.x + 86}" y="${r.y + 32}" width="8" height="26" rx="4" fill="${SH(.38)}"/>
    <rect x="${r.x + 38}" y="${r.y + 30}" width="52" height="15" rx="7" fill="${K.blush}"/>
    <rect x="${r.x + 40}" y="${r.y + 31}" width="22" height="4" rx="2" fill="#fff" opacity=".35"/>
    <rect x="${r.x + 20}" y="${r.y + 31}" width="20" height="12" rx="6" fill="${K.card}"/>
    <rect x="${r.x + 98}" y="${r.y + 8}" width="20" height="17" rx="3" fill="${K.lilac}"/><rect x="${r.x + 101}" y="${r.y + 11}" width="14" height="11" rx="1.5" fill="${K.card}"/>
    <path d="M${r.x + 101} ${r.y + 22} l5 -6 l3 3 l2 -2 l4 5 z" fill="${K.lilac}" opacity=".55"/></g>`,
  hall: (r) => `<g>
    <rect x="${r.x + 66}" y="${r.y + 10}" width="40" height="24" rx="5" fill="${SH(.4)}"/>
    <rect x="${r.x + 70}" y="${r.y + 14}" width="14" height="5" rx="2.5" fill="#fff" opacity=".22"/>
    <rect x="${r.x + 14}" y="${r.y + 36}" width="58" height="16" rx="7" fill="${SH(.3)}"/>
    <rect x="${r.x + 18}" y="${r.y + 42}" width="50" height="12" rx="5" fill="${SH(.25)}"/>
    <rect x="${r.x + 10}" y="${r.y + 38}" width="10" height="18" rx="5" fill="${SH(.36)}"/><rect x="${r.x + 66}" y="${r.y + 38}" width="10" height="18" rx="5" fill="${SH(.36)}"/>
    <rect x="${r.x + 98}" y="${r.y + 44}" width="12" height="12" rx="3" fill="${K.blush}" opacity=".8"/>
    <path d="M${r.x + 104} ${r.y + 44} q-8 -12 -2 -18 M${r.x + 104} ${r.y + 44} q6 -10 2 -16" fill="none" stroke="${SH(.4)}" stroke-width="2" stroke-linecap="round"/></g>`,
  kitchen: (r, act) => {
    const c = act ? K.card : SH(.25), d = act ? 'rgba(224,154,18,.5)' : SH(.38), b = r.y + r.h;
    return `<g>
    <rect x="${r.x + 6}" y="${b - 24}" width="34" height="24" rx="4" fill="${c}"/>
    <rect x="${r.x + 4}" y="${b - 27}" width="38" height="5" rx="2.5" fill="${d}"/>
    <path d="M${r.x + 12} ${b - 27} v-7 a2 2 0 0 1 2 -2 h14 a2 2 0 0 1 2 2 v7" fill="${act ? K.ink : SH(.4)}"/>
    <rect x="${r.x + 97}" y="${b - 52}" width="23" height="52" rx="5" fill="${c}"/><rect x="${r.x + 97}" y="${b - 32}" width="23" height="2" fill="${d}"/>
    <rect x="${r.x + 100}" y="${b - 46}" width="2.5" height="9" rx="1.2" fill="${d}"/><rect x="${r.x + 100}" y="${b - 26}" width="2.5" height="12" rx="1.2" fill="${d}"/></g>`;
  },
  corr: (r) => {
    const b = r.y + r.h;
    return `<g>
    <rect x="${r.x + 80}" y="${b - 58}" width="30" height="58" rx="5" fill="${SH(.32)}"/>
    <rect x="${r.x + 84}" y="${b - 54}" width="22" height="22" rx="3" fill="${SH(.12)}"/>
    <circle cx="${r.x + 101}" cy="${b - 26}" r="2.6" fill="${K.yellow}"/>
    <path d="M${r.x + 8} ${b} h12 v-12 h12 v-12 h12 v-12 h12" fill="none" stroke="${SH(.32)}" stroke-width="5" stroke-linejoin="round"/></g>`;
  },
  misc: (r) => {
    const b = r.y + r.h;
    return `<g>
    <rect x="${r.x + 14}" y="${b - 16}" width="16" height="16" rx="4" fill="${K.blush}" opacity=".8"/>
    <path d="M${r.x + 22} ${b - 16} q-10 -14 -3 -22 M${r.x + 22} ${b - 16} q8 -12 3 -20" fill="none" stroke="${SH(.4)}" stroke-width="2.4" stroke-linecap="round"/>
    <rect x="${r.x + 48}" y="${b - 26}" width="46" height="26" rx="6" fill="${SH(.28)}"/>
    <path d="M${r.x + 100} ${b} v-34" stroke="${SH(.35)}" stroke-width="3" stroke-linecap="round"/><path d="M${r.x + 92} ${b - 34} h16 l-4 -10 h-8 z" fill="${K.yellow}"/></g>`;
  },
};
const furnFor = (name) => /кух/i.test(name) ? 'kitchen' : /спал|детск/i.test(name) ? 'bed' : /зал|гост/i.test(name) ? 'hall' : /кор|прих/i.test(name) ? 'corr' : 'misc';

/** раскладка комнат: 2 колонки, нечётная последняя — во всю ширину. Возвращает [{name,x,y,w,h}] и высоту viewBox */
export const houseLayout = (names) => {
  const n = Math.max(1, Math.min(6, names.length)), rows = Math.ceil(n / 2), RH = 72, G = 8;
  const rooms = names.slice(0, 6).map((name, i) => {
    const row = Math.floor(i / 2), col = i % 2, last = i === n - 1 && n % 2 === 1;
    return { name, x: last ? 24 : col ? 154 : 24, y: 78 + row * (RH + G), w: last ? 252 : 122, h: RH };
  });
  const bottom = 78 + rows * (RH + G) - G;
  return { rooms, vbh: bottom + 26, bottom };
};

/**
 * house({ names, width, active, tone, checks, miniBug, labelPx }) → inline SVG (стиль 12/10b).
 * active — имя активной комнаты; tone — 'move' (жёлтая) | 'quiet' (мятная); miniBug — { pose, size }.
 */
export const house = ({ names, width = 300, active = '', tone = 'move', checks = false, miniBug = null, labelPx = 13 } = {}) => {
  const { rooms: R, vbh, bottom } = houseLayout(names);
  const s = width / 300, fs = +(labelPx / s).toFixed(2);
  const id = 'hs' + Math.round(width) + R.length + (checks ? 'c' : '');
  const actFill = tone === 'quiet' ? K.mintL : K.yellow, actEdge = tone === 'quiet' ? EDGE.mint : 'rgba(224,154,18,.45)';
  const rooms = R.map(r => {
    const act = r.name === active;
    const wall = act
      ? `<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="8" fill="${actFill}"/><rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="8" fill="url(#${id}ag)"/>
         <rect x="${r.x}" y="${r.y + r.h - 9}" width="${r.w}" height="9" rx="4" fill="${actEdge}"/>`
      : `<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="8" fill="${K.bg}"/><rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="8" fill="url(#${id}rg)"/>
         <rect x="${r.x}" y="${r.y + r.h - 9}" width="${r.w}" height="9" rx="4" fill="${SH(.07)}"/>`;
    const nm = r.name.length > 12 ? r.name.slice(0, 11) + '…' : r.name;
    const tw = nm.length * fs * 0.645 + fs * 0.9 + (checks ? fs * 1.45 : 0);
    const lh = fs * 1.55, lx = r.x + 6, ly = r.y + 6;
    const ck = checks ? `<circle cx="${lx + tw - fs * 0.85}" cy="${ly + lh / 2}" r="${fs * 0.58}" fill="${K.mint}"/>
      <path d="M${lx + tw - fs * 1.1} ${ly + lh / 2} l${fs * 0.22} ${fs * 0.22} l${fs * 0.4} -${fs * 0.42}" fill="none" stroke="#fff" stroke-width="${(2.6 / s).toFixed(2)}" stroke-linecap="round" stroke-linejoin="round"/>` : '';
    const label = `<rect x="${lx}" y="${ly}" width="${tw.toFixed(1)}" height="${lh.toFixed(1)}" rx="${(lh / 2).toFixed(1)}" fill="${K.card}"/>
      <text x="${lx + fs * 0.45}" y="${ly + lh * 0.7}" font-family="Nunito,sans-serif" font-weight="900" font-size="${fs}" fill="${K.ink}">${esc(nm)}</text>${ck}`;
    return { body: `<g>${wall}${FURN[furnFor(r.name)](r, act)}</g>`, label };
  });
  let mb = '';
  const ar = R.find(r => r.name === active);
  if (miniBug && ar) {
    const S = miniBug.size, kind = furnFor(ar.name);
    const cx = kind === 'kitchen' ? 68 : kind === 'bed' ? ar.w - 26 : ar.w * 0.5;
    const x = ar.x + cx - S / 2, y = ar.y + ar.h - 3 - S * 0.904;
    mb = bug({ pose: miniBug.pose, size: S, fx: null }).replace('<svg class="bug"', `<svg class="bug" x="${x.toFixed(1)}" y="${y.toFixed(1)}" overflow="visible"`);
  }
  return `<svg width="${width}" height="${(vbh * s).toFixed(1)}" viewBox="0 0 300 ${vbh}" aria-hidden="true" style="display:block;overflow:visible">
 <defs>
  <linearGradient id="${id}wg" x1="0" y1="0" x2=".35" y2="1"><stop offset="0" stop-color="${K.card}"/><stop offset="1" stop-color="${K.bg}"/></linearGradient>
  <linearGradient id="${id}rg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${SH(.07)}"/><stop offset=".5" stop-color="${SH(0)}"/></linearGradient>
  <radialGradient id="${id}ag" cx=".4" cy=".25" r=".8"><stop offset="0" stop-color="rgba(255,253,248,.7)"/><stop offset="1" stop-color="rgba(255,253,248,0)"/></radialGradient>
  <radialGradient id="${id}rf" cx=".3" cy=".2" r=".9"><stop offset="0" stop-color="rgba(255,253,248,.55)"/><stop offset=".6" stop-color="rgba(255,253,248,0)"/></radialGradient>
 </defs>
 <ellipse cx="150" cy="${bottom + 20}" rx="138" ry="6" fill="${SH(.12)}"/>
 <rect x="12" y="66" width="276" height="${bottom - 52 + 1}" rx="16" fill="${SH(.12)}"/>
 <rect x="12" y="62" width="276" height="${bottom - 52}" rx="16" fill="url(#${id}wg)"/>
 <path d="M10 70 L150 10 L290 70 Z" fill="${K.blush}" stroke="${K.blush}" stroke-width="14" stroke-linejoin="round"/>
 <path d="M10 70 L150 10 L290 70 Z" fill="url(#${id}rf)" stroke="none"/>
 <rect x="0" y="64" width="300" height="12" rx="6" fill="${K.blush}"/><rect x="0" y="70" width="300" height="6" rx="3" fill="${SH(.14)}"/>
 <path d="M136 54 a14 14 0 0 1 28 0 z" fill="${K.card}" stroke="${SH(.25)}" stroke-width="2" stroke-linejoin="round"/><path d="M150 40 v14" stroke="${SH(.25)}" stroke-width="2"/>
 ${rooms.map(r => r.body).join('')}
 ${mb}
 ${rooms.map(r => r.label).join('')}
</svg>`;
};

/* ---------- 14-events: крупная шкала ---------- */
const HATCH_OFF = `repeating-linear-gradient(-45deg, ${K.grey} 0 5px, ${K.greyL} 5px 10px)`;
const DOTS = `radial-gradient(circle, ${K.grey} 1.7px, transparent 2px) 0 0/7px 7px, ${K.greyL}`;
export const segFill = (st) => (st === 'offline' ? HATCH_OFF : st === 'nodata' ? DOTS : STATE[st].fill);
const pc = (h) => (h / 24 * 100).toFixed(2) + '%';
export const hhmm = (h) => `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60) % 60).padStart(2, '0')}`;
export const bigScale = ({ segs, now }) => {
  const ticks = [0, 6, 12, 18, 24].filter(h => now == null || Math.abs(h - now) > 2.6)
    .map((h, i, a) => `<span style="left:${pc(h)};${i === 0 ? 'transform:none' : i === a.length - 1 ? 'transform:translateX(-100%)' : ''}">${String(h).padStart(2, '0')}</span>`).join('');
  return `<div class="bday"><div style="position:relative"><div class="tr">${segs.map(([a, b, st]) => `<span style="left:${pc(a)};width:${pc(b - a)};background:${segFill(st)}"></span>`).join('')}</div>${now != null ? `<i class="now" style="left:${pc(now)}"></i>` : ''}</div>
   <div class="tk">${ticks}${now != null ? `<span class="nl" style="left:${pc(now)}">${hhmm(now)}</span>` : ''}</div></div>`;
};
export const EVENTS_CSS = `.bsc{margin-top:16px;padding:22px 20px 18px}
.bday .tr{position:relative;height:36px;border-radius:18px;overflow:hidden;background:var(--greyL)}
.bday .tr span{position:absolute;top:0;bottom:0}
.bday .now{position:absolute;top:-8px;width:5px;height:52px;margin-left:-2.5px;border-radius:3px;background:var(--ink)}
.bday .tk{position:relative;height:20px;margin-top:10px}
.bday .tk span{position:absolute;transform:translateX(-50%);font-size:15px;font-weight:800;color:var(--ink2);font-variant-numeric:tabular-nums}
.bday .tk .nl{color:var(--ink);font-weight:800}
.lg{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px 10px;margin-top:18px;padding-top:14px;border-top:1.5px solid rgba(43,38,69,.07)}
.lg span{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:800;color:var(--ink2);white-space:nowrap}
.lg b{width:14px;height:14px;border-radius:5px}`;

export function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]); }
