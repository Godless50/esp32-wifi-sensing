// «Жучок» — веб-интерфейс внутри Android (контракт v4/CONTRACT.md).
// window.app.setState(json) → перерисовка (только при изменении); действия → window.Android.send(action, jsonArgs).
// Экраны — функции от состояния; координаты внутри «холста» — как в макетах v4/prod (390×844).
import {
  K, ICON, STATE, HERO, RING_TOP, TOKENS, taskHeader, homeHeader, title, button, textLink, chip,
  eventsCard, dayScale, heroCard, ringTimer, timerPill, soundSticker, bug,
} from './ui.mjs';
import { resetIds } from './bug.mjs';
import * as P from './parts.mjs';
import { WALK } from './walk.mjs';
import { planSvg, PLAN_CSS, PLAN } from './plan.mjs';

/* ================= оболочка экрана ================= */
const APP_CSS = `${TOKENS}
html.walk,html.walk body{background:transparent!important}
html,body{margin:0;background:var(--bg);overscroll-behavior:none;-webkit-tap-highlight-color:transparent;user-select:none;-webkit-user-select:none}
#app{position:fixed;inset:0}
.scr{position:absolute;inset:0;display:flex;flex-direction:column;overflow:hidden}
.top{flex:none;padding-top:var(--top,24px);position:relative;z-index:3}
.top .hdr{position:relative;top:auto;left:auto;right:auto;margin:8px 20px 0}
.top .hdr.plain{gap:16px}
.mid{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;display:flex;flex-direction:column;-webkit-overflow-scrolling:touch}
.mid::before{content:'';flex:1 1 0;max-height:40px}.mid::after{content:'';flex:2 1 0}
.mid.start::before{display:none}
.stage{position:relative;flex:none;width:100%}
.sc{position:absolute;left:0;top:0;width:100%;transform-origin:0 0}
.cv{position:absolute;left:0;right:0;height:844px;overflow:hidden}
.c390{position:absolute;left:50%;top:0;width:390px;height:100%;margin-left:-195px}
.deco{position:absolute;left:50%;top:0;margin-left:-195px;pointer-events:none}
.flow{flex:none;padding:0 20px 24px}
.bot{flex:none;display:flex;flex-direction:column;padding:12px 20px calc(24px + env(safe-area-inset-bottom,0px));position:relative;z-index:3}
.bot .btn{position:relative;left:auto;right:auto;bottom:auto}
.bot .link{position:relative;left:auto;right:auto;margin-top:26px;margin-bottom:-6px}
.bot>.link:first-child{margin-top:0}
.btn.off{background:var(--greyL);color:var(--ink2);box-shadow:0 6px 0 var(--edgeGrey)}
[data-act],[data-ui]{cursor:pointer}
.ev .row span{overflow:hidden;text-overflow:ellipsis}
.hero .st .h1{font-size:clamp(22px,7.2vw,28px);padding:0 12px}
input,textarea{user-select:text;-webkit-user-select:text}
/* анимации: мягкое «дыхание» героя, вращение мини-кольца, пульс маячка/«сейчас» */
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.cv .art>svg.bug,.hero .art>svg.bug{animation:bob 2.6s ease-in-out infinite}
.spin{animation:spin 1.1s linear infinite}
.day .now{animation:pulse 2s ease-in-out infinite}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}`;

/** v: { bg, css, head, y0, h, cv, flow, bot, fit=true, start } → HTML экрана */
const shell = (v) => `<style>${v.css || ''}</style>
<div class="scr${v.cls ? ` ${v.cls}` : ''}" style="background:${v.bg || K.bg}">
 ${v.raw || ''}
 <div class="top">${v.head || ''}</div>
 <div class="mid${v.start ? ' start' : ''}">
  ${v.h ? `<div class="stage" data-h="${v.h}"${v.fit === false ? ' data-nofit' : ''} style="height:${v.h}px"><div class="sc"><div class="cv" style="top:${-v.y0}px;height:${v.y0 + v.h}px">${v.cv}</div></div></div>` : ''}
  ${v.flow ? `<div class="flow">${v.flow}</div>` : ''}
 </div>
 ${v.bot ? `<div class="bot">${v.bot}</div>` : ''}
</div>`;

const act = (html, a, args) => html.replace(/^<(\w+)/, `<$1 data-act="${a}"${args ? ` data-args='${P.esc(JSON.stringify(args))}'` : ''}`);
const backable = (h) => h.replace('aria-label="Назад"', 'aria-label="Назад" role="button" data-act="back"');
const tHead = (st, n, o = {}) => backable(taskHeader({ step: st.step?.n ?? n, part: st.step?.part ?? undefined, ...o }));
const plainHead = (t, back = true) => `<div class="hdr plain">${back ? `<div class="ibtn" aria-label="Назад" role="button" data-act="back">${ICON.back()}</div>` : ''}${t ? `<h1 class="h1">${t}</h1>` : ''}</div>`;
const c390 = (html) => `<div class="c390">${html}</div>`;
const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
const mmss = (s) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.max(0, Math.round(s)) % 60).padStart(2, '0')}`;

/* ---------- общие формы Wi-Fi ---------- */
const ssidField = (st, top) => P.inputField({ top, id: 'ssid', icon: ICON.wifi({ s: 22, w: 2.6 }), iconBg: K.mintL, label: 'Wi-Fi 2,4 ГГц', value: st.ssid || '', placeholder: 'Имя сети',
  extra: `<span class="chev" role="button" aria-label="Другая сеть" data-act="otherNetwork">${P.PIC.chevron({ s: 22, c: K.link, w: 2.8 })}</span>` });
const pwField = (st, top, label) => P.inputField({ top, id: 'pw', type: ui.showPw ? 'text' : 'password', icon: P.PIC.lock({ s: 22, w: 2.6 }), label, placeholder: 'Пароль', show: ui.showPw ? 'on' : true });
const submit = (label, a, o = {}) => button({ label, ...o }).replace('class="btn', `data-act="${a}" data-submit="1" class="btn`);

/* ================= экраны ================= */
const S = {};

S.hello = () => ({
  css: `.blob{position:absolute;left:50%;top:124px;width:340px;height:340px;transform:translateX(-50%);border-radius:50%;
 background:radial-gradient(circle at 38% 32%, rgba(255,253,248,.7) 0, rgba(255,253,248,0) 55%), ${K.yellow};box-shadow:0 8px 0 ${K.yellowD}}
.art{position:absolute;left:50%;top:112px;transform:translateX(-50%)}
.chips{position:absolute;left:12px;right:12px;top:592px;display:flex;justify-content:center;gap:6px;flex-wrap:wrap;row-gap:8px}
.chips .chip{font-size:14px;padding-right:12px;gap:6px}`,
  y0: 48, h: 588,
  cv: `${c390(`<svg class="deco" width="390" height="844" aria-hidden="true">
 <circle cx="40" cy="150" r="8" fill="${K.mint}"/><circle cx="354" cy="388" r="7" fill="${K.tomato}"/>
 <rect x="334" y="150" width="16" height="16" rx="5" fill="${K.lilac}" transform="rotate(20 342 158)"/>
 <rect x="26" y="372" width="18" height="10" rx="5" fill="${K.blush}" transform="rotate(-30 35 377)"/></svg>
<div class="blob"></div>
<div class="art">${bug({ pose: 'wave', size: HERO.big })}</div>
${soundSticker({ text: 'Привет, агент!', x: 196, y: 100, rot: 5 })}`)}
${title({ h: 'Я — Жучок', sub: 'Замечаю, когда дома кто-то есть', top: 500, xl: true })}
<div class="chips">${chip({ label: 'по Wi-Fi', icon: ICON.wifi({ s: 15, w: 2.8 }), iconBg: K.mintL })}${chip({ label: 'без камеры и микрофона', icon: ICON.camOff({ s: 15, w: 2.8 }), iconBg: K.lilacL })}</div>`,
  bot: act(button({ label: 'Задание №1', arrow: true }), 'start'),
});

const cableLike = (st, { head, h1, sub, btn, a }) => ({
  bg: K.lilacL, css: `.art{position:absolute;left:40px;top:256px}${P.SETUP_CSS}`,
  head, y0: 104, h: 572,
  cv: `${title({ h: h1, sub, top: 132 })}
${c390(`<div class="art">${bug({ pose: 'plug', size: HERO.mid })}</div>${P.phoneScene({ bx: 40, by: 256 })}`)}
${ssidField(st, 516)}
${pwField(st, 600, `Пароль ${P.onlyBug()}`)}`,
  bot: submit(btn, a, { arrow: true }),
});
S.cable = (st) => cableLike(st, { head: tHead(st, 1), h1: 'Подключи жучок', sub: 'Кабелем к телефону', btn: 'Записать в жучок', a: 'flash' });
const WN = { auth: ['Роутер не пустил', 'Пароль ещё разок?'], not_found: ['Не вижу сеть', 'Имя сети верное?'], cable: ['Не вижу жучок', 'Кабель до конца?'] };
S.wifi_new = (st) => {
  const e = WN[st.error?.kind] || (st.error ? ['Не вышло', P.esc(st.error.text || 'Ещё раз?')] : ['Новый Wi-Fi', 'Кабелем к телефону']);
  return cableLike(st, { head: plainHead(''), h1: e[0], sub: e[1], btn: 'Записать в жучок', a: 'newWifi' });
};

S.cable_missing = (st) => ({
  bg: K.err, css: `.art{position:absolute;left:40px;top:346px}${P.ASK_CSS}`,
  head: tHead(st, 1), y0: 104, h: 516,
  cv: `${title({ h: 'Не вижу жучок', sub: st.error?.kind === 'other' && st.error.text ? P.esc(st.error.text) : 'Кабель до конца?', top: 132 })}
${c390(`<div class="art">${bug({ pose: 'plug', size: HERO.mid, brows: 'sad', mouth: 'small', look: [3, -3] })}</div>${P.phoneScene({ bx: 40, by: 346, ask: true })}`)}`,
  bot: act(button({ label: 'Ещё раз' }), 'cableRetry'),
});

S.flashing = (st) => {
  const f = st.flash || {}, sg = Math.max(0, Math.min(2, f.stage ?? 0)), p = Math.max(0, Math.min(100, f.progress ?? 0)) / 100;
  const prog = [0.08, 0.1 + 0.8 * p, 0.95][sg];
  const stt = (i) => (i < sg ? 'done' : i === sg ? 'cur' : 'wait');
  const col = (i) => (stt(i) === 'cur' ? K.link : stt(i) === 'wait' ? K.grey : K.ink);
  return {
    bg: K.lilacL, css: `${P.SETUP_CSS}${P.STAGES_CSS}`,
    head: tHead(st, 1, { back: false }), y0: 104, h: 590,
    cv: `${title({ h: 'Вербуюсь…', sub: 'Это около минуты', top: 132 })}
${ringTimer({ top: RING_TOP, progress: prog, content: `<div class="art" style="top:50px">${bug({ pose: 'work', size: HERO.ring })}</div>` })}
${P.stages([
  { icon: P.PIC.plug({ s: 24, c: col(0), w: 2.6 }), label: 'связь', state: stt(0) },
  { icon: P.PIC.chip({ s: 24, c: col(1), w: 2.6 }), label: 'программа', state: stt(1) },
  { icon: ICON.wifi({ s: 24, c: col(2), w: 2.6 }), label: 'Wi-Fi', state: stt(2) },
], 608)}`,
    bot: `<div class="keep">${P.plugLock({ s: 40 })}Кабель не трогайте</div>`,
  };
};

S.wifi_error = (st) => {
  const kind = st.error?.kind || 'auth';
  const T = { auth: ['Роутер не пустил', 'Пароль ещё разок?'], not_found: ['Не вижу сеть', 'Имя сети верное?'] }[kind] || ['Не вышло', 'Ещё раз?'];
  const nf = kind === 'not_found';
  return {
    bg: K.err, css: `${P.SETUP_CSS}.art{position:absolute;left:50%;top:198px;transform:translateX(-50%)}`,
    head: tHead(st, 1), y0: 104, h: nf ? 538 : 524,
    cv: `${title({ h: T[0], sub: T[1], top: 132 })}
<div class="art">${bug({ pose: 'oops', size: HERO.mid })}</div>
${nf ? `${ssidField(st, 466)}${pwField(st, 556, 'Пароль')}`
  : `<div class="keep" style="top:458px">${P.plugLock({ s: 40 })}Кабель не трогайте</div>${pwField(st, 548, `Пароль от ${P.esc(st.ssid || 'сети')}`)}`}`,
    bot: `${submit('Проверить', 'retryWifi')}${act(textLink({ label: 'Другая сеть', icon: ICON.wifi({ s: 20, c: K.link, w: 2.6 }) }), 'otherNetwork')}`,
  };
};

const placeArt = (o) => `<div class="art">${bug({ pose: 'shelf', size: HERO.mid, ...o })}</div>`;
S.place = (st) => ({
  bg: K.lilacL, css: `${P.YN_CSS}.art{position:absolute;left:50%;top:206px;transform:translateX(-50%)}
.wait{position:absolute;left:20px;right:20px;top:652px;display:flex;justify-content:center;align-items:center;gap:10px;font-size:16px;font-weight:800;color:var(--ink2)}`,
  head: tHead(st, 2), y0: 104, h: 584,
  cv: `${title({ h: 'Поставь повыше', sub: 'Из телефона — в розетку', top: 132 })}
${placeArt()}
${P.yesNo([{ scene: 'shelf', ok: true, label: 'На полку' }, { scene: 'cupboard', ok: false, label: 'Не в шкаф' }, { scene: 'router', ok: false, label: 'Не вплотную' }], 486)}
<div class="wait">${P.miniRing()}Жду, когда появлюсь в сети…</div>`,
  bot: button({ label: 'Задание №3', arrow: true }).replace('class="btn"', 'class="btn off" aria-disabled="true"').replace(/stroke="#2B2645"/, `stroke="${K.ink2}"`),
});
S.place_online = (st) => ({
  bg: K.lilacL, css: `.art{position:absolute;left:50%;top:250px;transform:translateX(-50%)}
.net{position:absolute;left:0;right:0;top:552px;display:flex;justify-content:center}`,
  head: tHead(st, 2, { part: st.step?.part ?? 1 }), y0: 104, h: 492,
  cv: `${title({ h: 'Я в сети!', sub: 'Стою на месте и слушаю', top: 132 })}
${c390(`<div class="art">${bug({ pose: 'yay', scene: 'shelf', beacon: 'quiet', size: HERO.mid })}</div>${soundSticker({ text: 'Ура!', x: 272, y: 258, rot: 8, color: K.mintT })}`)}
<div class="net">${chip({ label: 'на связи', dot: K.mint, color: K.mintT, bg: K.mintL })}</div>`,
  bot: act(button({ label: 'Задание №3', arrow: true }), 'placeNext'),
});
S.place_late = (st) => ({
  bg: K.err, css: `.art{position:absolute;left:50%;top:198px;transform:translateX(-50%)}${P.CHECK_CSS}`,
  head: tHead(st, 2), y0: 104, h: 594,
  cv: `${title({ h: 'Ближе к роутеру?', sub: 'Жучок не выходит на связь', top: 132 })}
${placeArt({ body: 'grey', beacon: 'offline', blush: false, eyes: 'open', look: [0, 2], mouth: 'line', ant: 'droop', arms: ['down', 'down'], fx: 'nowifi' })}
${P.checkList({ top: 502 })}`,
  bot: act(button({ label: 'Позвать ещё раз' }), 'placeRecheck'),
});

const HELPER_ICONS = ['tv', 'laptop', 'speaker', 'router'];
S.silence = (st) => {
  const sv = st.survey || {}, left = sv.secondsLeft ?? 60, n = Math.max(0, sv.helpers ?? 0), running = sv.running !== false && !sv.done;
  const helpers = [...Array(Math.min(3, n))].map((_, i) => ({ icon: HELPER_ICONS[i % 4], found: true }));
  if (running && n < 4 && (n > 0 || left > 30)) helpers.push({ found: false });
  const angles = n >= 3 || !running ? [214, 246, 150, 124] : n === 2 ? [214, 246, 124] : n === 1 ? [214, 124] : [124];
  const note = n > 0 ? `<b>${ICON.check({ s: 13, c: '#fff', w: 3.6 })}</b>${n} ${plural(n, 'помощник', 'помощника', 'помощников')} рядом`
    : (!running || left <= 30) ? 'Помощников нет — справлюсь' : 'Ищу помощников…';
  return {
    bg: K.lilacL, css: `.note{position:absolute;left:0;right:0;top:640px;display:flex;justify-content:center;align-items:center;gap:8px;font-size:16px;font-weight:800;color:var(--ink2)}
.note b{width:22px;height:22px;border-radius:11px;background:var(--mint);display:grid;place-items:center}`,
    head: tHead(st, 3, { part: st.step?.part ?? Math.min(1, 1 - left / 60) }), y0: 104, h: 562,
    cv: `${title({ h: 'Минута тишины', sub: 'Замрите. Я слушаю дом.', top: 120 })}
${c390(`${ringTimer({ top: RING_TOP, progress: Math.min(1, Math.max(0, 1 - left / 60)),
  content: `<div class="art" style="top:50px">${bug({ pose: 'shh', size: HERO.ring })}</div>`, helpers, angles })}
${soundSticker({ text: 'Тсс…', x: 26, y: 246, rot: -8, color: K.link })}
${timerPill({ top: RING_TOP + 340 - 26, text: mmss(left) })}`)}
<div class="note">${note}</div>`,
    bot: act(textLink({ label: 'Кто-то прошёл? Заново', icon: ICON.retry({ s: 20, c: K.link, w: 2.6 }) }), 'surveyRestart'),
  };
};

S.walk_intro = (st) => {
  const plus = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${K.link}" stroke-width="3.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`;
  const rooms = ['Зал', 'Кухня', 'Спальня', 'Прихожая'].map(r => `<span class="hit">${chip({ label: r, dot: K.lilac })}</span>`).join('')
    + `<span class="hit">${chip({ label: 'комната', icon: plus, iconBg: K.lilacL, color: K.link })}</span>`;
  return {
    bg: K.lilacL, css: P.WALK_INTRO_CSS,
    head: tHead(st, 4, { part: st.step?.part ?? 0 }), y0: 104, h: 496,
    cv: `${title({ h: 'Обход', top: 120 })}
<div class="dur">${chip({ label: '≈20 мин', icon: P.PIC.clock({ s: 16, w: 2.8 }), iconBg: 'transparent' })}</div>
<div class="board">${P.STEPS.map(P.walkTile).join('')}
 <span class="ar" style="left:160px;top:39px">${P.arrow(0)}</span>
 <span class="ar" style="right:60px;top:111px">${P.arrow(90)}</span>
 <span class="ar" style="left:160px;bottom:39px">${P.arrow(180)}</span>
 <div class="tok">${bug({ pose: 'point', size: 56, look: [3, -2] })}</div>
</div>
<div class="rms">${rooms}</div>`,
    bot: `${act(button({ label: 'Начать', arrow: true }), 'walkStart')}${act(textLink({ label: 'Не сейчас' }), 'walkLater')}`,
  };
};

S.camera_denied = (st) => ({
  bg: K.err, css: `${P.REC_CSS}.art{position:absolute;left:50%;top:240px;transform:translateX(-50%)}
.rc{position:absolute;left:0;right:0;top:528px;display:flex;justify-content:center}`,
  head: tHead(st, 4, { part: st.step?.part ?? 0 }), y0: 104, h: 470,
  cv: `${title({ h: 'Без камеры не&nbsp;отмечу углы', top: 120 })}
<div class="art">${bug({ pose: 'oops', size: HERO.mid })}</div>
<div class="rc">${P.recOffChip()}</div>`,
  bot: act(button({ label: `${P.PIC.cam({ s: 22, w: 2.6 })}Разрешить` }), 'askCamera'),
});

/** недостающие разрешения (обучение и главный): одна карточка, строки-действия */
const checklistCard = (st) => {
  const c = st.checklist || {}, rows = [];
  if (c.notifications === false) rows.push(['askNotifications', P.PIC.bell, 'Включить уведомления']);
  if (c.battery === false) rows.push(['askBattery', P.PIC.battery, 'Не выключать меня']);
  return rows.length ? `<div class="card ev perm">${rows.map(([a, f, l]) => `<div class="row" role="button" data-act="${a}"><i style="background:${K.lilacL}">${f({ s: 20 })}</i><span>${l}</span>${P.PIC.chevR({ s: 20, c: K.ink2, w: 2.6 })}</div>`).join('')}</div>` : '';
};

S.learning = (st) => {
  const t = st.train || {};
  return {
    bg: K.lilacL, css: `.eta{position:absolute;left:50%;transform:translateX(-50%);height:52px;padding:0 22px 0 18px;border-radius:26px;background:var(--card);
 display:flex;align-items:center;gap:8px;font-size:17px;font-weight:900;color:var(--ink);white-space:nowrap;box-shadow:var(--edge),var(--soft)}
.perm{margin-top:4px}`,
    head: tHead(st, 5, { back: false, part: st.step?.part ?? t.progress ?? 0 }), y0: 104, h: 504,
    cv: `${title({ h: 'Учусь…', top: 120 })}
${ringTimer({ top: RING_TOP, progress: t.progress ?? 0, content: `<div class="art" style="top:50px">${bug({ pose: 'work', size: HERO.ring })}</div>` })}
<div class="eta" style="top:${RING_TOP + 340 - 26}px">${P.PIC.clock({ s: 20, c: K.lilac, w: 2.6 })}до 10 минут</div>`,
    flow: checklistCard(st),
  };
};

S.learn_failed = (st) => ({
  bg: K.err, css: `${P.ROUTE_ROW_CSS}.art{position:absolute;left:50%;top:224px;transform:translateX(-50%)}`,
  head: tHead(st, 5, { back: false, part: st.step?.part ?? 1 }), y0: 104, h: 494,
  cv: `${title({ h: 'Не вышло', sub: 'Пройдём обход ещё раз?', top: 120 })}
<div class="art">${bug({ pose: 'oops', size: HERO.mid })}</div>
${P.routeRow(520)}`,
  bot: act(button({ label: `${ICON.retry({ s: 22, w: 2.8 })}Обход заново` }), 'learnRetry'),
});

S.result = (st) => {
  const r = st.result || {}, rooms = Array.isArray(r.rooms) && r.rooms.length > 1 ? r.rooms : null;
  const ok = (v) => v == null || v >= 0.6;
  const hw = rooms ? Math.min(240, 236 * 300 / P.houseLayout(rooms).vbh) : 0;
  return {
    bg: K.lilacL, css: `${P.SK_CSS}
.disc{position:absolute;left:50%;top:222px;width:240px;height:240px;transform:translateX(-50%);border-radius:50%;
 background:radial-gradient(circle at 38% 32%, rgba(255,253,248,.95) 0, rgba(255,253,248,.55) 60%, rgba(255,253,248,.3) 100%);box-shadow:0 6px 0 var(--edgeLilac)}
.art{position:absolute;left:50%;top:204px;transform:translateX(-50%)}
.hs{position:absolute;left:12px;top:${rooms ? (470 - P.houseLayout(rooms).vbh * hw / 300).toFixed(1) : 0}px}
.art2{position:absolute;left:236px;top:326px}`,
    y0: 48, h: 700,
    cv: `${title({ h: 'Готово!', sub: 'Вот что я умею', top: 92, xl: true })}
${c390(`${P.CONFETTI}${rooms
  ? `<div class="hs">${P.house({ names: rooms, width: hw, checks: true })}</div><div class="art2">${bug({ pose: 'medal', size: 146 })}</div>`
  : `<div class="disc"></div><div class="art">${bug({ pose: 'medal', size: HERO.mid })}</div>`}`)}
${P.skills({ rooms: rooms ? rooms.join(', ').toLowerCase() : false, presence: ok(r.presence), motion: ok(r.motion), style: 'position:absolute;left:20px;right:20px;top:528px' })}`,
    bot: act(button({ label: 'В мой дом', arrow: true }), 'openHome'),
  };
};

/* ---------- главный ---------- */
const DAY_MAP = { moving: 'move', move: 'move', quiet: 'quiet', empty: 'nobody', nobody: 'nobody', offline: 'offline', nodata: 'nodata' };
const daySegs = (h) => (h.day || []).map(([a, b, s]) => [a, b, DAY_MAP[s] || 'quiet']).filter(([a, b]) => b > a);
const EV = {
  move: (e) => (e.t2 ? ['move', 'Двигались', `${e.t}–${e.t2}`] : ['move', 'Двигаются', `с ${e.t}`]),
  arrive: (e) => ['in', 'Кто-то пришёл', e.t],
  empty: (e) => ['nobody', 'Никого дома', e.t],
  offline: (e) => (e.t2 ? ['offline', 'Жучок молчал', `${e.t}–${e.t2}`] : ['offline', 'Жучок молчит', `с ${e.t}`]),
  back: (e) => ['back', 'Снова на связи', e.t],
};
const evRows = (list) => (list || []).filter(e => EV[e.kind]).slice().sort((a, b) => (b.t || '').localeCompare(a.t || ''))
  .map(e => { const [kind, label, time] = EV[e.kind](e); return { kind, label: P.esc(label + (e.zone ? ` · ${e.zone}` : '')), time: P.esc(time) }; });

const LOC = { 'кухня': 'на кухне', 'зал': 'в зале', 'спальня': 'в спальне', 'коридор': 'в коридоре', 'прихожая': 'в прихожей', 'детская': 'в детской',
  'гостиная': 'в гостиной', 'кабинет': 'в кабинете', 'ванная': 'в ванной', 'балкон': 'на балконе', 'комната': 'в комнате', 'холл': 'в холле', 'столовая': 'в столовой' };
const whereTitle = (zone) => { const l = LOC[String(zone).trim().toLowerCase()]; return l ? `Кто-то ${l}` : `Кто-то: ${P.esc(zone)}`; };

const HOME_CSS = `${PLAN_CSS}
.sec{position:absolute;left:20px;right:20px;display:flex;align-items:center;justify-content:space-between;height:48px}
.sec b{font-size:17px;font-weight:900}.sec a{font-weight:800;font-size:15px;color:var(--link);height:48px;display:flex;align-items:center}
.hero .hs{position:absolute;left:50%;top:22px;transform:translateX(-50%)}
.hero .sticker.r{left:auto!important;right:16px}
.flow .ev,.flow .perm{margin-top:12px}
.flow .perm .row i{background:var(--lilacL)}
.wait{height:56px;display:flex;align-items:center;justify-content:center;gap:10px;margin-top:12px;
 font-size:16px;font-weight:800;color:var(--ink2);border:2px dashed var(--edgeMint);border-radius:26px}
.wait i{display:flex;gap:4px}.wait i b{width:6px;height:6px;border-radius:3px;background:var(--mint);animation:pulse 1.4s ease-in-out infinite}
.wait i b:nth-child(2){animation-delay:.2s}.wait i b:nth-child(3){animation-delay:.4s}`;
const HL = { heroTop: 148, heroH: 392, bugTop: 22 };

S.home = (st) => {
  const h = st.home || {}, s = h.state || 'hark';
  const status = s === 'offline' ? 'offline' : s === 'away' ? 'away' : 'online';
  const head = homeHeader({ status }).replace('aria-label="Настройки"', 'aria-label="Настройки" role="button" data-act="openSettings"');
  const secHtml = (link = true) => `<div class="sec" style="top:552px"><b>Сегодня</b>${link ? '<a data-act="openEvents" role="button">Все события</a>' : ''}</div>`;
  const scale = (segs) => dayScale({ now: h.now ?? 12, segs, style: 'position:absolute;left:20px;right:20px;top:600px' });
  const rows = evRows(h.events).slice(0, 3);
  const evCard = rows.length ? eventsCard({ rows }) : '';
  const base = { css: HOME_CSS, head, y0: 132, h: 528, fit: false, start: true };

  if (s === 'offline') {
    return { ...base, h: 618, css: `${HOME_CSS}${P.CHECK_CSS}.sec2{position:absolute;left:20px;right:20px;top:508px;height:40px;display:flex;align-items:center;font-size:17px;font-weight:900}`,
      cv: `${heroCard({ state: 'offline', pose: 'silent', top: HL.heroTop, height: 352, zone: '', title: 'Жучок молчит', pill: h.since ? `с ${P.esc(h.since)}` : '', bugSize: HERO.mid, bugTop: -6 })}
<div class="sec2">Что проверить</div>${P.checkList({ top: 548 })}`,
      bot: act(button({ label: 'Позвать ещё раз' }), 'placeRecheck') };
  }
  if (s === 'away') {
    const art = `<div style="position:absolute;left:50%;top:22px;transform:translateX(-50%);width:260px;height:236px">
<svg width="260" height="236" viewBox="0 0 260 236" aria-hidden="true" style="position:absolute;inset:0">
 <ellipse cx="130" cy="214" rx="122" ry="20" fill="${K.mintL}"/>
 <g fill="none" stroke="${K.mint}" stroke-width="7" stroke-linecap="round"><path d="M98 40 Q130 14 162 40"/><path d="M110 54 Q130 38 150 54"/></g>
 <circle cx="130" cy="66" r="5.5" fill="${K.mint}"/>
 <rect x="62" y="116" width="136" height="98" rx="12" fill="${K.card}"/>
 <path d="M50 124 L130 76 L210 124" fill="none" stroke="${K.blush}" stroke-width="18" stroke-linecap="round" stroke-linejoin="round"/>
 <rect x="82" y="138" width="30" height="28" rx="8" fill="${K.lilacL}"/><rect x="148" y="138" width="30" height="28" rx="8" fill="${K.lilacL}"/>
</svg>
<div style="position:absolute;left:50%;bottom:-6px;transform:translateX(-50%)">${bug({ pose: 'listen', size: 128, fx: null })}</div></div>`;
    return { ...base,
      cv: `${heroCard({ state: 'nodata', zone: '', top: HL.heroTop, height: HL.heroH, art, title: 'Ты не дома', sub: 'Вернёшься —<br>снова буду слушать' })}${secHtml()}${scale(daySegs(h))}`,
      flow: `${checklistCard(st)}${evCard}` };
  }
  if (s === 'hark') {
    return { ...base,
      cv: `${heroCard({ state: 'quiet', pose: 'hark', top: HL.heroTop, height: HL.heroH, title: 'Прислушиваюсь…', bugSize: HERO.mid, bugTop: HL.bugTop + 24 })}${secHtml(false)}${scale(daySegs(h))}`,
      flow: `${checklistCard(st)}<div class="wait"><i><b></b><b style="opacity:.6"></b><b style="opacity:.3"></b></i>Пока тихо, слушаю</div>` };
  }
  const V = {
    moving: { state: 'move', pose: 'alert', title: 'Кто-то дома', pill: 'двигается', icon: ICON.move({ s: 20, c: K.tomatoT }), tone: 'move' },
    quiet: { state: 'quiet', pose: 'listen', title: 'Кто-то дома', pill: 'тихо', icon: ICON.home({ s: 20, c: K.mintT }), tone: 'quiet' },
    empty: { state: 'nobody', pose: 'bored', title: 'Никого дома', pill: 'скучаю', icon: ICON.homeEmpty({ s: 20, c: K.link }) },
  }[s] || null;
  if (!V) return { ...base, cv: '', flow: '' };
  const rooms = Array.isArray(h.rooms) && h.rooms.length > 1 ? h.rooms : null;
  const zone = rooms && s !== 'empty' && h.zone && rooms.includes(h.zone) ? h.zone : '';
  const t = zone ? whereTitle(zone) : V.title;
  const plan = h.plan && Array.isArray(h.plan.rooms) && h.plan.rooms.length ? h.plan : null;
  let hero;
  if (plan) {
    hero = heroCard({ state: V.state, top: HL.heroTop, height: HL.heroH, zone: '', title: t, pill: V.pill, pillIcon: V.icon,
      art: `<div class="hs" role="button" data-act="openMap">${planSvg({ plan, dot: s !== 'empty' ? h.dot : null, active: zone, tone: V.tone || 'nobody', w: 300, h: 236 })}</div>`,
      sticker: s === 'moving' ? { text: 'Засёк!', x: 12, y: 18, rot: -7, color: K.tomatoT } : null });
  } else if (rooms) {
    const vbh = P.houseLayout(rooms).vbh, w = Math.min(304, 252 * 300 / vbh);
    hero = heroCard({ state: V.state, top: HL.heroTop, height: HL.heroH, zone: '', title: t, pill: V.pill, pillIcon: V.icon,
      art: `<div class="hs">${P.house({ names: rooms, width: w, active: zone, tone: V.tone, miniBug: zone ? { pose: V.pose === 'alert' ? 'alert' : 'listen', size: 84 } : null })}</div>`,
      sticker: s === 'moving' ? { text: 'Засёк!', x: 12, y: 24, rot: -7, color: K.tomatoT } : null });
  } else {
    hero = heroCard({ state: V.state, pose: V.pose, top: HL.heroTop, height: HL.heroH, title: t, pill: V.pill, pillIcon: V.icon,
      bugSize: HERO.mid, bugTop: HL.bugTop, bugOpts: s === 'moving' ? { fx: null } : {},
      sticker: s === 'moving' ? { text: 'Засёк!', x: 246, y: 20, rot: 6, color: K.tomatoT } : null }).replace('class="sticker"', 'class="sticker r"');
  }
  return { ...base, cv: `${hero}${secHtml()}${scale(daySegs(h))}`, flow: `${checklistCard(st)}${evCard}` };
};

S.map = (st) => {
  const h = st.home || {}, s = h.state || 'hark', plan = h.plan;
  const tone = s === 'moving' ? 'move' : s === 'empty' ? 'nobody' : 'quiet';
  const pill = { moving: ['двигается', K.tomatoT, K.yellow], quiet: ['тихо', K.mintT, K.mintL], empty: ['никого', K.link, K.lilacL] }[s];
  const zone = Array.isArray(h.rooms) && h.rooms.length > 1 && h.zone ? h.zone : '';
  return {
    css: `${PLAN_CSS}.mapc{margin-top:16px;padding:12px;border-radius:28px;background:var(--card);box-shadow:var(--edge),var(--soft);display:flex;justify-content:center}
.mapc .plan{max-width:100%;height:auto}.leg{display:flex;gap:8px;justify-content:center;margin-top:16px;flex-wrap:wrap}`,
    head: plainHead('Карта'), start: true,
    flow: plan ? `<div class="mapc">${planSvg({ plan, dot: s === 'moving' || s === 'quiet' ? h.dot : null, active: zone, tone, w: 326, h: 440 })}</div>
<div class="leg">${pill ? chip({ label: zone ? `${pill[0]} · ${P.esc(zone)}` : pill[0], dot: pill[1], color: pill[1], bg: pill[2] }) : ''}${h.dot && h.err ? chip({ label: `точность ±${String(Math.round(h.err * 10) / 10).replace('.', ',')} м` }) : ''}</div>`
      : `<div class="card" style="margin-top:20px;padding:18px;text-align:center;color:var(--ink2);font-weight:800">Плана пока нет</div>`,
  };
};

S.events = (st) => {
  const h = st.home || {}, segs = daySegs(h);
  const legend = [['quiet', 'тихо'], ['move', 'движение'], ['nobody', 'никого'], ['offline', 'нет связи']]
    .map(([k, l]) => `<span><b style="background:${P.segFill(k)}"></b>${l}</span>`).join('');
  const rows = evRows(h.events);
  return {
    css: P.EVENTS_CSS,
    head: plainHead('События'), start: true,
    flow: `<div class="card bsc">${P.bigScale({ segs, now: h.now })}<div class="lg">${legend}</div></div>
${rows.length ? eventsCard({ rows, style: 'margin-top:20px' }) : `<div class="card" style="margin-top:20px;padding:18px;text-align:center;color:var(--ink2);font-weight:800">Пока тихо</div>`}`,
  };
};

S.settings = (st) => {
  const boards = st.boards || [];
  const tile = (b) => {
    const open = ui.openMac === b.mac;
    return `<div class="tile big${open ? ' open' : ''}"><div class="hd" role="button" data-ui="board" data-mac="${P.esc(b.mac)}">
  <i class="av">${bug({ pose: b.online ? 'listen' : 'silent', size: 66, fx: '' })}</i>
  <span><b>${P.esc(b.name || 'Жучок')}</b><div class="st">${b.online ? chip({ label: 'на связи', dot: K.mint, color: K.mintT, bg: K.mintL }) : chip({ label: 'не на связи', dot: K.grey, color: K.ink2, bg: K.greyL })}</div></span>${(open ? P.PIC.up : P.PIC.chevR)({ s: 22, c: K.ink2, w: 2.6 })}</div>
  ${open ? `<div class="rm" role="button" data-act="removeBoard" data-args='${P.esc(JSON.stringify({ mac: b.mac }))}'><i>${P.PIC.trash({ s: 20, c: K.link })}</i>Убрать жучок</div>` : ''}</div>`;
  };
  const tileAct = (icon, label, a) => `<div class="tile" role="button" data-act="${a}"><i style="background:${K.lilacL}">${icon}</i><span><b>${label}</b></span>${P.PIC.chevR({ s: 22, c: K.ink2, w: 2.6 })}</div>`;
  return {
    css: `.sec{font-size:17px;font-weight:900;margin:24px 0 12px}.sec:first-child{margin-top:16px}
.list{display:flex;flex-direction:column;gap:12px}
.tile{display:flex;align-items:center;gap:14px;height:72px;padding:0 16px 0 12px;border-radius:24px;background:var(--card);box-shadow:var(--edge),var(--soft)}
.tile i{flex:none;width:48px;height:48px;border-radius:16px;display:grid;place-items:center;box-shadow:0 4px 0 var(--edgeLilac)}
.tile span{flex:1;min-width:0}
.tile b{display:block;font-weight:800;font-size:17px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tile.big{display:block;height:auto;padding:0 16px 0 14px}
.tile .hd{display:flex;align-items:center;gap:14px;height:96px}
.tile .rm{display:flex;align-items:center;gap:12px;height:56px;border-top:1.5px solid rgba(43,38,69,.07);font-weight:800;font-size:16px;color:var(--link)}
.tile .rm i{width:36px;height:36px;border-radius:12px;background:var(--lilacL);box-shadow:none}
.tile .av{width:68px;height:68px;border-radius:22px;background:radial-gradient(circle at 40% 30%, var(--card) 0, var(--mintL) 70%);box-shadow:0 4px 0 var(--edgeMint)}
.tile .st{margin-top:6px}.tile .st .chip{height:30px;font-size:14px;box-shadow:none}
.tile.add{background:transparent;border:2.5px dashed var(--grey);box-shadow:none}
.tile.add i{background:var(--yellow);box-shadow:0 4px 0 var(--yellowD)}
.ver{height:48px;display:grid;place-items:center;font-size:13px;font-weight:700;color:var(--ink2);margin-top:16px}`,
    head: plainHead('Настройки'), start: true,
    flow: `<div class="sec">Жучки</div><div class="list">${boards.map(tile).join('')}
 <div class="tile add" role="button" data-act="addBoard"><i>${P.PIC.plus({ s: 24, w: 3 })}</i><span><b>Добавить жучок</b></span></div></div>
<div class="sec">Дом</div><div class="list">
 ${tileAct(P.PIC.wifiKey({ s: 24 }), 'Новый пароль Wi-Fi', 'newWifi')}
 ${tileAct(P.PIC.route({ s: 24 }), 'Пройти обход заново', 'rewalk')}
</div>
<div class="ver" data-long="computerMode">Жучок ${P.esc(st.version || '1.0')}</div>`,
  };
};

Object.assign(S, WALK); // обход поверх камеры (walk.html)

/* ================= отрисовка ================= */
const ui = { showPw: false, openMac: null };
let state = null, lastJson = '', lastScreen = '';
const root = () => document.getElementById('app');

function view(st) {
  const f = S[st.screen] || S.hello;
  return shell(f(st));
}

function render() {
  if (!state) return;
  resetIds();
  if (state.screen !== lastScreen) { ui.showPw = false; ui.openMac = null; }
  const html = view(state);
  const r = root();
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  if (state.screen !== lastScreen || !r.firstChild) r.replaceChildren(tpl.content);
  else morph(r, tpl.content);
  lastScreen = state.screen;
  const wl = document.getElementById('wallLen');   // поле длины: показываем новое значение, если его не правят
  if (wl && document.activeElement !== wl && wl.dataset.v) wl.value = wl.dataset.v;
  fit();
}

/** аккуратное обновление DOM: совпадающие узлы не трогаем (поля ввода, фокус, анимации остаются) */
function morph(a, b) {
  const ac = [...a.childNodes], bc = [...b.childNodes];
  if (ac.length !== bc.length) { a.replaceChildren(...bc); return; }
  for (let i = 0; i < ac.length; i++) {
    const x = ac[i], y = bc[i];
    if (x.isEqualNode(y)) continue;
    if (x.nodeType !== y.nodeType || x.nodeName !== y.nodeName) { x.replaceWith(y); continue; }
    if (x.nodeType === 3 || x.nodeType === 8) { x.data = y.data; continue; }
    if (x.nodeType === 1) {
      for (const at of [...x.attributes]) if (!y.hasAttribute(at.name)) x.removeAttribute(at.name);
      for (const at of [...y.attributes]) {
        if (x.tagName === 'INPUT' && at.name === 'value') continue; // введённое пользователем не перетираем
        if (x.getAttribute(at.name) !== at.value) x.setAttribute(at.name, at.value);
      }
      morph(x, y);
    }
  }
}

/** холст середины: если не влезает по высоте — уменьшаем до 0,8, дальше прокрутка */
function fit() {
  for (const st of root().querySelectorAll('.stage[data-h]')) {
    const H = +st.dataset.h, sc = st.firstElementChild, mid = st.parentElement;
    let s = 1;
    if (!st.hasAttribute('data-nofit')) {
      const flow = mid.querySelector('.flow');
      const avail = mid.clientHeight - (flow ? flow.offsetHeight : 0);
      s = Math.max(0.8, Math.min(1, avail / H));
    }
    st.style.height = `${(H * s).toFixed(1)}px`;
    sc.style.transform = s < 1 ? `scale(${s.toFixed(4)})` : '';
    sc.style.width = s < 1 ? `${(100 / s).toFixed(3)}%` : '';
  }
}

/* ================= действия ================= */
function send(action, args = {}) {
  const json = JSON.stringify(args);
  if (window.Android && typeof window.Android.send === 'function') window.Android.send(action, json);
  else console.log('[Android.send]', action, json);
}

function doAction(el) {
  const a = el.dataset.act;
  if (el.getAttribute('aria-disabled') === 'true') return;
  let args = el.dataset.args ? JSON.parse(el.dataset.args) : {};
  if (el.dataset.submit && (a === 'flash' || a === 'retryWifi' || a === 'newWifi')) {
    const ssidEl = document.getElementById('ssid'), pwEl = document.getElementById('pw');
    const ssid = (ssidEl ? ssidEl.value : state?.ssid || '').trim();
    if (!ssid) { ssidEl?.focus(); return; }
    args = { ssid, password: pwEl ? pwEl.value : '' };
    if (pwEl) { pwEl.value = ''; pwEl.blur(); } // пароль сразу стираем, нигде не храним
  }
  if (a === 'planSave') {
    if (PLAN.bad().length) { render(); return; }
    args = { rooms: PLAN.result() };
  }
  if (el.dataset.submit && a === 'walkRoom') {
    const r = document.getElementById('room'), name = (r ? r.value : '').trim();
    if (!name) { r?.focus(); return; }
    args = { name }; r.value = ''; r.blur();
  }
  send(a, args);
}

function onClick(e) {
  const u = e.target.closest('[data-ui]');
  if (u) {
    e.preventDefault();
    if (u.dataset.ui === 'togglePw') {
      ui.showPw = !ui.showPw;
      const pw = document.getElementById('pw');
      if (pw) { pw.type = ui.showPw ? 'text' : 'password'; pw.focus(); }
      u.textContent = ui.showPw ? 'скрыть' : 'показать';
    } else if (u.dataset.ui === 'board') { ui.openMac = ui.openMac === u.dataset.mac ? null : u.dataset.mac; render(); }
    else if (u.dataset.ui.startsWith('plan') && PLAN.click(u)) render();
    return;
  }
  const el = e.target.closest('[data-act]');
  if (el) { e.preventDefault(); doAction(el); }
}

let longT = 0;
function onDown(e) {
  if (PLAN.down(e)) { e.preventDefault(); return; }
  const el = e.target.closest('[data-long]');
  if (el) longT = setTimeout(() => send(el.dataset.long), 800);
}
const onUp = () => { clearTimeout(longT); if (PLAN.up()) render(); };
function onMove(e) { if (PLAN.move(e)) { e.preventDefault(); render(); } }
function onChange(e) { if (e.target.id === 'wallLen' && PLAN.setLen(e.target.value)) render(); }

function onKey(e) {
  if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
    const b = root().querySelector('[data-submit]');
    if (e.target.id === 'ssid') document.getElementById('pw')?.focus();
    else if (e.target.id === 'wallLen') { if (PLAN.setLen(e.target.value)) { e.target.blur(); render(); } }
    else if (e.target.id === 'room') { const g = root().querySelector('[data-act="walkRoom"][data-submit]'); if (g) doAction(g); }
    else if (b) doAction(b);
  }
}

/* ================= API для Kotlin ================= */
function setState(s) {
  if (typeof s === 'string') { try { s = JSON.parse(s); } catch { return; } }
  if (!s || typeof s !== 'object') return;
  const json = JSON.stringify(s);
  if (json === lastJson) return;
  lastJson = json; state = s;
  render();
}

function boot() {
  if (!document.getElementById('app-css')) {
    const st = document.createElement('style'); st.id = 'app-css'; st.textContent = APP_CSS; document.head.appendChild(st);
  }
  const r = root();
  r.addEventListener('click', onClick);
  r.addEventListener('pointerdown', onDown); r.addEventListener('pointerup', onUp); r.addEventListener('pointercancel', onUp); r.addEventListener('pointerleave', onUp);
  r.addEventListener('keydown', onKey);
  r.addEventListener('pointermove', onMove); r.addEventListener('change', onChange);
  window.addEventListener('resize', fit);
  const q = (window.app && window.app.q) || [];
  window.app = { setState, getState: () => state, send };
  q.forEach(setState);
}
boot();
