// «Жучок» — экраны обхода поверх камеры (задание 4 из 5). Макеты: v4/prod/07a0, 07a, 07b, 07d, 08, 08b.
// Страница прозрачная (walk.html): под ней — изображение камеры из CaptureActivity. Прицел — на 45 % высоты,
// там же, куда Kotlin пускает луч до пола. Состояние: st.walk (см. CaptureActivity.walkState).
import { K, EDGE, ICON, RING_TOP, HERO, taskHeader, title, button, textLink, chip, ringTimer, timerPill, soundSticker, bug } from './ui.mjs';
import * as P from './parts.mjs';
import { edit, syncEdit, editorSvg, PLAN, fmtM, len } from './plan.mjs';

const act = (html, a, args) => html.replace(/^<(\w+)/, `<$1 data-act="${a}"${args ? ` data-args='${P.esc(JSON.stringify(args))}'` : ''}`);
const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 10 || h >= 20) ? b : c; };
const c390 = (html) => `<div class="c390">${html}</div>`;
const mmss = (s) => `${Math.floor(Math.max(0, s) / 60)}:${String(Math.max(0, Math.round(s)) % 60).padStart(2, '0')}`;
const head = (w, o = {}) => taskHeader({ step: 4, part: w.part ?? 0, ...o }).replace('aria-label="Назад"', 'aria-label="Назад" role="button" data-act="walkBack"');

export const pinIcon = `<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 11 L12 21" stroke="${K.ink}" stroke-width="2.8" stroke-linecap="round"/><circle cx="12" cy="7.5" r="5.5" fill="${K.ink}"/><circle cx="10.4" cy="6" r="1.6" fill="${K.yellow}"/></svg>`;
const pauseIcon = `<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4.2" height="14" rx="2" fill="${K.ink}"/><rect x="13.8" y="5" width="4.2" height="14" rx="2" fill="${K.ink}"/></svg>`;
const plusIcon = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${K.ink}" stroke-width="3" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`;
const linkIc = (d) => `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${K.link}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const skipIc = linkIc('<path d="M5 6l7 6-7 6M13 6l6 6-6 6"/>');
const exitIc = linkIc('<path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4"/><path d="M21 12H10M16 7l5 5-5 5"/>');

/** прицел: ok — белое кольцо и жёлтая точка; иначе — пунктир */
const aim = (ok) => `<svg class="aim" width="120" height="120" viewBox="0 0 120 120" aria-hidden="true">
 <circle cx="60" cy="60" r="34" fill="none" stroke="${K.ink}" stroke-opacity=".35" stroke-width="7"/>
 <circle cx="60" cy="60" r="34" fill="none" stroke="#fff" stroke-width="3.5"${ok ? '' : ' stroke-dasharray="8 8"'}/>
 <g stroke="#fff" stroke-width="3.5" stroke-linecap="round"><path d="M60 14 L60 4"/><path d="M60 106 L60 116"/><path d="M14 60 L4 60"/><path d="M106 60 L116 60"/></g>
 <circle cx="60" cy="60" r="6" fill="${ok ? K.yellow : K.grey}" stroke="${K.ink}" stroke-width="2.5"/></svg>`;
const AIM_HINT = { lost: 'Поводи телефоном', nofloor: 'Покажи мне пол', far: 'Ближе к углу у пола' };

const WALK_CSS = `
.scr.cam{background:transparent!important}
.scrim{position:absolute;left:0;right:0;top:0;height:230px;background:linear-gradient(rgba(43,38,69,.6),rgba(43,38,69,0));pointer-events:none}
.cam .hdr .prog .cap{color:#fff}
.cam .hdr .prog .seg i{background:rgba(255,253,248,.35)}.cam .hdr .prog .seg i.on{background:${K.card}}
.chip.rec{gap:8px;padding-left:6px;box-shadow:var(--edge)}
.recb{position:relative;width:auto!important;height:24px;padding:0 7px 0 6px;border-radius:12px!important;background:${K.greyL};display:flex!important;align-items:center;gap:4px;font-style:normal;font-size:13px;font-weight:900;color:${K.ink2};letter-spacing:.02em}
.recb b{width:9px;height:9px;border-radius:50%;background:${K.tomato}}
.recb s{position:absolute;left:3px;right:3px;top:11px;height:2px;border-radius:1px;background:${K.ink};transform:rotate(-12deg)}
.wrow{margin:8px 20px 0;display:flex}
.rooms{margin:12px 20px 0;height:48px;display:flex;gap:8px}
.rooms .rs{flex:1;min-width:0;display:flex;gap:6px;overflow:hidden;-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 24px),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - 24px),transparent)}
.rooms .hit{flex:none;height:48px;display:flex;align-items:center}
.rooms .chip{box-shadow:var(--edge)}
.chip.now{background:${K.yellow};padding-left:8px;box-shadow:0 4px 0 ${K.yellowD}!important}
.chip.now i{width:24px;height:24px;border-radius:12px;background:${K.ink}}
.chip.now i:after{content:'';display:block;width:8px;height:8px;margin:8px;border-radius:4px;background:${K.yellow}}
.chip.plus{width:48px;justify-content:center;padding:0}
.toast{position:absolute;left:0;right:0;top:calc(45% + 66px);display:flex;justify-content:center;pointer-events:none;z-index:2}
.toast span{padding:8px 14px;border-radius:18px;background:rgba(43,38,69,.82);color:#fff;font-weight:800;font-size:15px}
.aim{position:absolute;left:50%;top:45%;margin:-60px 0 0 -60px;pointer-events:none}
.aimhint{position:absolute;left:0;right:0;top:calc(45% + 66px);display:flex;justify-content:center;pointer-events:none}
.aimhint span{padding:8px 14px;border-radius:18px;background:rgba(43,38,69,.72);color:#fff;font-weight:800;font-size:15px}
.dim{position:absolute;inset:0;background:rgba(43,38,69,.6);z-index:4}
.cam .bot{z-index:5}
.sheet .lk{border-top:1.5px solid rgba(43,38,69,.08)}.sheet .lk .link{height:56px;margin:0!important}
.sheet .btn+.lk{border-top:0;margin-top:8px}
.stk{position:absolute;right:18px;top:-22px;padding:8px 14px;border-radius:14px;background:var(--card);font-family:Unbounded,sans-serif;font-weight:800;font-size:16px;color:${K.link};transform:rotate(5deg);box-shadow:var(--edge),var(--soft)}
.wait2{display:flex;align-items:center;justify-content:center;gap:10px;height:56px;font-size:16px;font-weight:800;color:var(--ink2)}
.find i.grey{background:${K.greyL};box-shadow:0 3px 0 ${EDGE.grey}}
.sheet{position:relative;border-radius:28px;background:var(--card);box-shadow:0 6px 0 ${EDGE.ink},0 8px 24px rgba(43,38,69,.2);padding:18px 16px 22px}
.sheet .btn{position:relative;left:auto;right:auto;bottom:auto}
.sheet .link{position:relative;left:auto;right:auto;bottom:auto!important;top:auto!important}
.sheet .h1{text-align:center;margin:6px 0 18px}
.sheet .row{display:flex;align-items:center;justify-content:space-between;min-height:32px;margin:0 4px 16px;gap:8px}
.sheet .row b{font-size:18px;font-weight:800}.sheet .row b span{color:var(--ink2);font-weight:800}
.sheet .note{margin:12px 4px 0;text-align:center;font-size:15px;font-weight:800;color:var(--ink2)}
.find{display:flex;align-items:center;gap:12px;margin:0 4px 18px}
.find i{flex:none;width:56px;height:56px;border-radius:18px;background:var(--mintL);display:grid;place-items:center;box-shadow:0 3px 0 ${EDGE.mint}}
.find b{font-size:20px;font-weight:800;line-height:1.2}.find em{display:block;font-style:normal;font-size:15px;font-weight:700;color:var(--ink2)}
.bds{display:flex;gap:6px}
.bd{width:26px;height:26px;border-radius:13px;display:grid;place-items:center;font-style:normal;font-size:13px;font-weight:900;color:var(--ink2);border:2.5px dashed var(--grey);box-sizing:border-box}
.bd.on{border:0;color:var(--ink);background:radial-gradient(circle at 35% 30%,${K.yellow} 20%,${K.yellowD} 100%)}
.bd.cur{border:2.5px solid var(--lilac);color:var(--link);background:var(--lilacL)}
.task{display:flex;align-items:center;gap:10px;min-height:104px;margin-bottom:16px}
.task .mb{flex:none;width:64px;height:64px;border-radius:20px;background:var(--mintL);display:grid;place-items:center;box-shadow:0 3px 0 ${EDGE.mint};overflow:hidden}
.task .tx{flex:1;min-width:0}
.task .tx b{display:block;font-size:20px;font-weight:800;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.task .tx span{display:block;margin-top:2px;font-size:16px;font-weight:700;color:var(--ink2);line-height:1.25}
.mring{flex:none;position:relative;width:104px;height:104px}
.mring svg{position:absolute;inset:0}
.mring b{position:absolute;inset:0;display:grid;place-items:center;font-family:Unbounded,sans-serif;font-weight:700;font-size:26px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.names{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:0 0 14px}
.names .chip{height:44px;font-size:16px;box-shadow:var(--edge)}
.nm{display:flex;gap:8px;align-items:center;height:56px;padding:0 6px 0 16px;border-radius:20px;background:var(--bg);box-shadow:inset 0 0 0 2px ${EDGE.lilac};margin-bottom:14px}
.nm input{flex:1;min-width:0;border:0;background:transparent;font:800 17px Nunito,sans-serif;color:var(--ink);outline:none}
.nm .go{flex:none;width:44px;height:44px;border-radius:14px;background:var(--yellow);display:grid;place-items:center;box-shadow:0 3px 0 ${K.yellowD}}
.ring>svg circle:nth-of-type(2)[stroke-dasharray^="0.0 "]{display:none}
.ring .pt{position:absolute;width:96px;height:76px;margin:-38px 0 0 -48px;border-radius:18px;background:var(--card);display:grid;place-items:center;border:2px solid ${EDGE.lilac};box-shadow:0 3px 0 ${EDGE.lilac}}
.done{position:absolute;left:50%;top:${RING_TOP + 340 - 26}px;transform:translateX(-50%);height:52px;padding:0 22px 0 16px;border-radius:26px;background:var(--card);
 display:flex;align-items:center;gap:10px;font-size:17px;font-weight:900;white-space:nowrap;box-shadow:var(--edge),var(--soft)}
.done b{width:28px;height:28px;border-radius:14px;background:var(--mint);display:grid;place-items:center}`;

const recChip = () => `<div class="wrow"><span class="chip rec"><i class="recb"><b></b>REC<s></s></i>видео не пишу</span></div>`;
const toast = (w) => (w.msg ? `<div class="toast"><span>${P.esc(w.msg)}</span></div>` : '');

/** чипы комнат: готовые — с галочкой, текущая — жёлтая; «+» — следующая комната */
const roomsRow = (w, { plus = false } = {}) => {
  const rs = (w.rooms || []).map(r => `<span class="hit">${r.name === w.active ? `<span class="chip now"><i></i>${P.esc(r.name)}</span>`
    : r.done ? chip({ label: P.esc(r.name), icon: ICON.check({ s: 14, c: '#fff', w: 3.6 }), iconBg: K.mint })
    : chip({ label: P.esc(r.name), dot: K.grey })}</span>`).join('');
  if (!rs && !plus) return '';
  return `<div class="rooms"><div class="rs">${rs}</div>${plus ? `<span class="hit" role="button" aria-label="Ещё комната" data-act="walkAddRoom"><span class="chip plus">${plusIcon}</span></span>` : ''}</div>`;
};

const camTop = (w, o = {}) => `${head(w)}${o.rooms === false ? '' : roomsRow(w, o)}${recChip()}`;
const cam = (w, { top, raw = '', sheet, rooms, plus }) => ({
  bg: 'transparent', css: WALK_CSS, cls: 'cam',
  raw: `<div class="scrim"></div>${w.msg ? raw.replace(/<div class="aimhint">[\s\S]*?<\/div>/, '') : raw}${toast(w)}`,
  head: top ?? camTop(w, { rooms, plus }),
  bot: `<div class="sheet">${sheet}</div>`,
});

/* ---------- 07a0: найти жучок ---------- */
const findPic = `<svg width="34" height="34" viewBox="0 0 32 32" aria-hidden="true"><rect x="2" y="2" width="28" height="28" rx="8" fill="${K.card}"/>
 <rect x="9" y="5" width="14" height="22" rx="4" fill="${K.mint}"/><circle cx="16" cy="11" r="2.6" fill="${K.mintL}"/></svg>`;
const walk_find = (st) => {
  const w = st.walk || {}, b = w.board;
  const many = (w.boardsTotal || 0) > 1;
  return cam(w, {
    rooms: false,
    sheet: b
      ? `<div class="find"><i>${findPic}</i><b>Поднеси к жучку${many ? `<em>${P.esc(b.name)} · ${b.i} из ${w.boardsTotal}</em>` : ''}</b></div>
${act(button({ label: 'Тут жучок' }), 'walkBoard')}`
      : `<div class="find"><i class="grey">${bug({ pose: 'silent', size: 52, fx: '' })}</i><b>Жучок молчит<em>Он в розетке?</em></b></div>
<div class="wait2">${P.miniRing()}Ищу…</div>`,
  });
};

/* ---------- имя комнаты ---------- */
const walk_room = (st) => {
  const w = st.walk || {}, used = new Set((w.rooms || []).map(r => r.name.toLowerCase()));
  const first = !(w.rooms || []).some(r => r.done);
  const names = (w.names || ['Зал', 'Кухня', 'Спальня', 'Прихожая', 'Детская', 'Коридор']).filter(n => !used.has(n.toLowerCase())).slice(0, 6);
  const go = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="${K.ink}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`;
  return cam(w, {
    sheet: `<h2 class="h1">${first ? 'Где ты сейчас?' : 'Куда дальше?'}</h2>
<div class="names">${names.map(n => act(`<span role="button">${chip({ label: P.esc(n), dot: K.lilac })}</span>`, 'walkRoom', { name: n })).join('')}</div>
<div class="nm"><input id="room" maxlength="24" placeholder="Своё название" autocomplete="off"><span class="go" role="button" aria-label="Дальше" data-act="walkRoom" data-submit="1">${go}</span></div>
${first ? '' : act(textLink({ label: 'Все комнаты отмечены', icon: ICON.check({ s: 20, c: K.link, w: 2.8 }) }), 'walkScenario')}`,
  });
};

/* ---------- 07a: углы ---------- */
const walk_mark = (st) => {
  const w = st.walk || {}, n = w.corners || 0, ok = w.aim === 'ok';
  const shown = Math.max(4, n + 1);
  const beads = Array.from({ length: Math.min(shown, 8) }, (_, i) => `<i class="bd${i < n ? ' on' : i === n ? ' cur' : ''}">${i + 1}</i>`).join('');
  return cam(w, {
    plus: true,
    raw: `${aim(ok)}${!ok && AIM_HINT[w.aim] ? `<div class="aimhint"><span>${AIM_HINT[w.aim]}</span></div>` : ok && n === 0 ? '<div class="aimhint"><span>Угол у самого пола</span></div>' : ''}`,
    sheet: `<div class="row"><b>${P.esc(w.active || '')} <span>· угол ${n + 1}</span></b><div class="bds">${beads}</div></div>
${ok ? act(button({ label: `${pinIcon}Тут угол` }), 'walkCorner') : button({ label: `${pinIcon}Тут угол` }).replace('class="btn"', 'class="btn off" aria-disabled="true"')}
${n >= 3 ? `<div class="lk">${act(textLink({ label: 'Комната готова', icon: ICON.check({ s: 20, c: K.link, w: 2.8 }) }), 'walkRoomDone')}</div>` : ''}`,
  });
};

/* ---------- 07b: задание по комнате ---------- */
const TASK = {
  walk: ['point', 'медленно вдоль&nbsp;стен'], stand: ['listen', 'встань посередине', 'Замри!'], sit: ['shh', 'сядь и посиди', 'Тсс…'],
  tour: ['point', 'по очереди'], walk2: ['point', 'ещё разок походи'],
};
const walk_task = (st) => {
  const w = st.walk || {}, t = w.task || {}, [pose, what, stk] = TASK[t.kind] || TASK.walk;
  const R = 44, Lc = 2 * Math.PI * R, prog = t.total ? Math.max(0, Math.min(1, 1 - t.left / t.total)) : 0;
  const ring = `<div class="mring"><svg viewBox="0 0 104 104" width="104" height="104">
 <circle cx="52" cy="52" r="${R}" fill="none" stroke="${K.lilacL}" stroke-width="10"/>
 <circle cx="52" cy="52" r="${R}" fill="none" stroke="${K.lilac}" stroke-width="10" stroke-linecap="round" stroke-dasharray="${(Lc * prog).toFixed(1)} 999" transform="rotate(-90 52 52)"/></svg>
 <b>${mmss(t.left ?? 0)}</b></div>`;
  return cam(w, {
    sheet: `${stk ? `<div class="stk">${stk}</div>` : ''}<div class="task"><div class="mb">${bug({ pose, size: 56, fx: '' })}</div>
 <div class="tx"><b>${t.room ? `${P.esc(t.room)}:` : 'Все комнаты'}</b><span>${what}</span></div>${ring}</div>
${act(button({ label: `${pauseIcon}Пауза`, variant: 'secondary' }), 'walkPause')}`,
  });
};

/* ---------- 07d: пауза ---------- */
const walk_pause = (st) => {
  const w = st.walk || {}, task = w.pause === 'task';
  return {
    ...cam(w, {
      raw: '<div class="dim"></div>',
      sheet: `<h2 class="h1">Пауза</h2>
${act(button({ label: 'Дальше', arrow: true }), 'walkResume')}
${task ? `<div class="lk">${act(textLink({ label: 'Эту комнату заново', icon: ICON.retry({ s: 20, c: K.link, w: 2.6 }) }), 'walkRedo')}</div>
<div class="lk">${act(textLink({ label: 'Пропустить комнату', icon: skipIc }), 'walkSkip')}</div>` : ''}
<div class="lk">${act(textLink({ label: 'Выйти', icon: exitIc }), 'walkExit')}</div>`,
    }),
  };
};

/* ---------- 08: все за дверь ---------- */
const phoneOnTable = (w = 56) => `<svg width="${w}" height="${(w * 84 / 148).toFixed(0)}" viewBox="0 0 148 84" aria-hidden="true">
 <path d="M30 50 L26 79 M118 50 L122 79" stroke="${K.ink}" stroke-width="8" stroke-linecap="round"/>
 <path d="M28 18 L120 18 Q126 18 128 24 L136 44 Q138 50 132 50 L16 50 Q10 50 12 44 L20 24 Q22 18 28 18 Z" fill="${K.yellow}"/>
 <path d="M12 47 Q12 50 16 50 L132 50 Q136 50 136 47 L136 54 Q136 57 132 57 L16 57 Q12 57 12 54 Z" fill="${K.yellowD}"/>
 <g transform="translate(74 34) scale(1 .52) rotate(-24)">
  <rect x="-17" y="-30" width="34" height="60" rx="9" fill="${K.ink}" transform="translate(0 6)" opacity=".18"/>
  <rect x="-17" y="-30" width="34" height="60" rx="9" fill="${K.ink}"/>
  <rect x="-12.5" y="-25.5" width="25" height="51" rx="5.5" fill="${K.lilac}"/>
  <rect x="-9" y="-22" width="8" height="24" rx="3" fill="#fff" opacity=".35"/>
 </g></svg>`;
const RC = 170, RR = RC - 22, RA = 236 * Math.PI / 180;
const tableOnRing = `<div class="pt" style="left:${(RC + RR * Math.sin(RA)).toFixed(0)}px;top:${(RC - RR * Math.cos(RA)).toFixed(0)}px">${phoneOnTable(78)}</div>`;

const walk_out = (st) => {
  const w = st.walk || {}, o = w.out || {}, going = !!o.going, total = o.total || 120, left = o.left ?? total;
  return {
    bg: K.lilacL, css: WALK_CSS, head: head(w), y0: 104, h: 562,
    cv: `${title({ h: 'Все за дверь!', sub: going ? 'Слушаю пустой дом' : 'Вернитесь через 2 минуты', top: 120 })}
${c390(`${ringTimer({ top: RING_TOP, progress: going ? Math.max(0, Math.min(1, 1 - left / total)) : 0,
  content: `<div class="art" style="top:46px;margin-left:-4px">${bug({ pose: 'door', size: HERO.ring })}</div>${tableOnRing}` }).replace(/(stroke-dasharray="0\.0 9999")/, '$1 opacity="0"')}
${soundSticker({ text: 'Пока-пока!', x: 22, y: 246, rot: -8, color: K.link })}
${timerPill({ top: RING_TOP + 340 - 26, text: mmss(left) })}`)}`,
    bot: going
      ? act(textLink({ label: 'Кто-то остался? Заново', icon: ICON.retry({ s: 20, c: K.link, w: 2.6 }) }), 'walkAgain')
      : act(button({ label: `${ICON.doorIn({ s: 22, w: 2.6 })}Уходим` }), 'walkGo'),
  };
};

/* ---------- 08b: заходите ---------- */
const walk_back = (st) => {
  const w = st.walk || {}, last = !!w.last, nr = (w.rooms || []).length;
  return {
    bg: K.lilacL, css: WALK_CSS, head: head(w, { back: !last }), y0: 104, h: 562,
    cv: `${title({ h: last ? 'Обход готов!' : 'Заходите!', sub: last ? 'Весь дом запомнил' : 'Пустой дом запомнил', top: 120 })}
${c390(`${ringTimer({ top: RING_TOP, progress: 1, content: `<div class="art" style="top:50px">${bug({ pose: 'yay', size: HERO.ring })}</div>` })}`)}
<div class="done"><b>${ICON.check({ s: 16, c: '#fff', w: 3.6 })}</b>${last ? `${nr} ${plural(nr, 'комната', 'комнаты', 'комнат')}` : mmss(w.out?.total || 120)}</div>`,
    bot: last ? act(button({ label: 'Учись!', arrow: true }), 'walkSave') : act(button({ label: 'Дальше', arrow: true }), 'walkGo'),
  };
};

/* ---------- план перед сохранением: стены можно поправить ---------- */
const ED_CSS = `.edw{display:flex;justify-content:center;margin-top:4px}
.edw .ed{max-width:100%;height:auto}
.pt1{text-align:center;margin:8px 0 2px}
.lenrow{display:flex;align-items:center;gap:10px;margin:0 4px 6px}
.lenrow .pm{flex:none;width:56px;height:56px;border-radius:18px;background:var(--lilacL);display:grid;place-items:center;font:900 26px Nunito,sans-serif;color:var(--link);box-shadow:0 3px 0 ${EDGE.lilac}}
.lenrow .in{flex:1;min-width:0;height:56px;border-radius:18px;background:var(--bg);box-shadow:inset 0 0 0 2px ${EDGE.lilac};display:flex;align-items:center;padding:0 16px}
.lenrow input{flex:1;min-width:0;border:0;background:transparent;font:800 22px Unbounded,sans-serif;color:var(--ink);outline:none;text-align:center}
.lenrow em{font-style:normal;font-weight:900;color:var(--ink2)}
.sheet .x{width:40px;height:40px;border-radius:14px;background:var(--mint);display:grid;place-items:center}
.ed-r{margin:10px 0 0;justify-content:center}.ed-r .rs{flex:none;-webkit-mask-image:none;mask-image:none;flex-wrap:wrap;justify-content:center}
.perr{text-align:center;font-weight:800;color:${K.tomatoT};margin:0 0 10px}
.lk .link{position:relative;left:auto;right:auto;bottom:auto!important;top:auto!important}
.lks{display:flex;justify-content:center;gap:4px}.lks .lk{border-top:0!important}.lks .link{padding:0 10px}`;
const walk_plan = (st) => {
  const w = st.walk || {};
  syncEdit(w.plan);
  const sel = edit.sel && edit.rooms[edit.sel.r];
  const tl = (label, ui, icon) => `<div class="lk">${textLink({ label, icon }).replace('class="link"', `data-ui="${ui}" class="link"`)}</div>`;
  const ortho = () => `<div class="lks">${tl('Выровнять', 'planOrtho', ICON.check({ s: 20, c: K.link, w: 2.8 }))}${tl('Как было', 'planReset', ICON.retry({ s: 20, c: K.link, w: 2.6 }))}</div>`;
  let bot;
  if (sel) {
    const i = edit.sel.i, L = len(sel.poly[i], sel.poly[(i + 1) % sel.poly.length]);
    bot = `<div class="sheet"><div class="row"><b>${P.esc(sel.name)} <span>· стена ${i + 1}</span></b><span class="x" role="button" data-ui="planDone">${ICON.check({ s: 20, c: '#fff', w: 3.4 })}</span></div>
<div class="lenrow"><span class="pm" role="button" data-ui="planMinus">−</span><span class="in"><input id="wallLen" inputmode="decimal" enterkeyhint="done" autocomplete="off" data-v="${L.toFixed(2).replace('.', ',')}" value="${L.toFixed(2).replace('.', ',')}"><em>м</em></span><span class="pm" role="button" data-ui="planPlus">+</span></div>
${ortho()}</div>`;
  } else {
    const bad = PLAN.bad();
    bot = `${bad.length ? `<div class="perr">«${P.esc(bad[0])}» слишком маленькая</div>` : ''}${act(button({ label: 'Сохранить план', arrow: true }), 'planSave')}<div style="margin-top:6px">${ortho()}</div>`;
  }
  return {
    bg: K.lilacL, css: WALK_CSS + ED_CSS, head: head(w), start: true,
    flow: `<h1 class="h1 pt1">Проверь план</h1>
<div class="rooms ed-r"><div class="rs">${edit.rooms.map((r, i) => `<span class="hit" role="button" data-ui="planRoom" data-r="${i}">${i === edit.cur
  ? `<span class="chip now"><i></i>${P.esc(r.name)}</span>` : chip({ label: P.esc(r.name), dot: K.lilac })}</span>`).join('')}</div></div>
<div class="edw">${editorSvg()}</div>`,
    bot,
  };
};

/* ---------- ждём связь с центром ---------- */
const walk_wait = (st) => cam(st.walk || {}, { rooms: false, sheet: `<div class="find"><i>${P.miniRing()}</i><b>Секунду…</b></div>` });

export const WALK = { walk_find, walk_room, walk_mark, walk_task, walk_pause, walk_out, walk_back, walk_wait, walk_plan };
