// СГЕНЕРИРОВАНО node v4/app/sync.mjs из v4/prod/_src/bug.mjs — не править руками
// Жучок v4 (prod) — генератор персонажа. bug({ pose, size, beacon }) → строка <svg>.
// Правила: STYLE.md. Тело-яйцо 1:1,1 (низ шире), настоящие надкрылья со швом, 6 лапок,
// маячок на макушке = цвет состояния, свет сверху-слева, без контуров и фильтров.

export const COLORS = {
  ink: '#2B2645', ink2: '#5B5573', bg: '#FFF6E6', card: '#FFFDF8',
  mint: '#3DD3B0', mintD: '#1EA488', mintT: '#137A66', mintL: '#D9F7EE',
  yellow: '#FFD23F', yellowD: '#E09A12',
  tomato: '#FF5A47', tomatoT: '#C7321F',
  lilac: '#7A6FF0', lilacL: '#ECE9FF', link: '#4A40C8',
  grey: '#AFA7C2', greyL: '#F1EEF6', err: '#FFE7D6', blush: '#FF8FA8',
};
const K = COLORS;

// Маячок: ключ состояния или hex
export const BEACON = { quiet: K.mint, move: K.tomato, nobody: K.lilac, offline: K.grey };

const BODY = {
  mint: { s0: '#D9F7EE', s1: K.mint, s2: K.mintD, under: K.mintD, spot: K.mintD },
  grey: { s0: K.greyL, s1: K.grey, s2: K.ink2, under: K.grey, spot: K.ink2 },
};

// ---------- позы ----------
// eyes: open|wide|half|closed|happy|up|look ; mouth: smile|o|small|flat|grin|shh|hmm|wobble|line
// ant: up|perk|droop|tilt|kink ; arms: [левая, правая] ; open: надкрылья раскрыты
export const POSES = {
  wave:    { name: 'машет', note: 'привет', beacon: 'quiet', eyes: 'open', look: [2, -1], mouth: 'grin', ant: 'tilt', arms: ['down', 'waveR'] },
  listen:  { name: 'слушаю', note: 'дома тихо', beacon: 'quiet', eyes: 'open', look: [4, 0], mouth: 'smile', ant: 'tilt', arms: ['down', 'down'], fx: 'waves' },
  alert:   { name: 'засёк!', note: 'движение', beacon: 'move', eyes: 'wide', mouth: 'o', ant: 'perk', brows: 'up', arms: ['waveL', 'waveR'], open: true, fx: 'flash' },
  bored:   { name: 'скучаю', note: 'никого', beacon: 'nobody', eyes: 'half', look: [-2, 0], mouth: 'flat', ant: 'droop', arms: ['down', 'down'], fx: 'dots' },
  silent:  { name: 'молчит', note: 'нет связи', beacon: 'offline', body: 'grey', eyes: 'open', look: [0, 2], mouth: 'line', ant: 'droop', arms: ['down', 'down'], blush: false, fx: 'nowifi' },
  hark:    { name: 'прислушиваюсь', note: 'после запуска', beacon: 'quiet', eyes: 'up', look: [3, 0], mouth: 'small', ant: 'tilt', arms: ['down', 'cupR'], fx: 'dotwaves' },
  yay:     { name: 'ура!', note: 'получилось', beacon: 'quiet', eyes: 'happy', mouth: 'grin', ant: 'perk', arms: ['waveL', 'waveR'], open: true, fx: 'confetti' },
  work:    { name: 'работаю', note: 'загрузка, учёба', beacon: 'nobody', eyes: 'lens', mouth: 'hmm', ant: 'up', brows: 'focus', arms: ['hipsL', 'lensR'], prop: 'lens' },
  oops:    { name: 'ой', note: 'ошибка', beacon: 'nobody', eyes: 'wide', look: [0, 2], mouth: 'wobble', ant: 'kink', brows: 'sad', arms: ['headL', 'down'], fx: 'drop' },
  plug:    { name: 'со штекером', note: 'кабель', beacon: 'quiet', eyes: 'open', look: [3, -2], mouth: 'smile', ant: 'tilt', arms: ['down', 'holdR'], prop: 'plug' },
  shelf:   { name: 'машет с полки', note: 'поставь на место', beacon: 'quiet', eyes: 'open', look: [0, 2], mouth: 'grin', ant: 'up', arms: ['down', 'waveR'], scene: 'shelf' },
  point:   { name: 'указывает', note: 'обход', beacon: 'quiet', eyes: 'open', look: [6, 0], mouth: 'smile', ant: 'tilt', brows: 'focus', arms: ['hipsL', 'pointR'] },
  door:    { name: 'машет у двери', note: 'все выходят', beacon: 'quiet', eyes: 'happy', mouth: 'smile', ant: 'tilt', arms: ['waveL', 'down'], scene: 'door' },
  medal:   { name: 'с медалью', note: 'итог', beacon: 'quiet', eyes: 'happy', mouth: 'grin', ant: 'perk', arms: ['down', 'holdR'], prop: 'medal' },
  shh:     { name: 'тсс', note: 'тишина', beacon: 'quiet', eyes: 'up', mouth: 'shh', ant: 'tilt', arms: ['down', 'shh'], fx: 'waves' },
};

let uid = 0;
/** app: сброс счётчика id перед каждой отрисовкой — одинаковое состояние даёт одинаковый SVG (DOM не дёргается) */
export const resetIds = () => { uid = 0; };
const EX = [97, 143], EY = 150, MY = 181;

export function bug(opts = {}) {
  const P = { ...(POSES[opts.pose || 'wave'] || POSES.wave), ...opts };
  const size = P.size ?? 240;
  const small = size < 64;
  const id = 'jk' + (++uid);
  const beacon = BEACON[P.beacon] || P.beacon || K.mint;
  const B = BODY[P.body || 'mint'];
  const N = K.ink;
  const look = P.look || [0, 0];

  // --- усики ---
  const ANT = {
    up:    [['M108 48 Q100 26 86 12', 86, 10], ['M132 48 Q140 26 154 12', 154, 10]],
    perk:  [['M110 48 Q106 22 100 2', 100, 0], ['M130 48 Q134 22 140 2', 140, 0]],
    droop: [['M108 48 Q88 26 66 38', 64, 40], ['M132 48 Q152 26 174 38', 176, 40]],
    tilt:  [['M108 48 Q96 22 74 16', 72, 15], ['M132 48 Q142 24 156 10', 156, 8]],
    kink:  [['M108 48 L96 30 L106 22 L92 6', 92, 4], ['M132 48 Q150 30 168 32', 170, 33]],
  }[P.ant || 'up'];
  const stalks = ANT.map(([d]) => `<path d="${d}" fill="none" stroke="${N}" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>`).join('');
  const beads = ANT.map(([, x, y]) => `<circle cx="${x}" cy="${y}" r="10.5" fill="url(#${id}bd)"/>${small ? '' : `<circle cx="${x - 3.5}" cy="${y - 3.5}" r="3.2" fill="#fff" opacity=".85"/>`}`).join('');

  // --- лапки (6): 2 ручки, 2 боковые, 2 ступни ---
  const limb = (d, hx, hy) => `<path d="${d}" fill="none" stroke="${N}" stroke-width="11" stroke-linecap="round"/><circle cx="${hx}" cy="${hy}" r="8.5" fill="${N}"/>`;
  const ARM = {
    down:  () => limb('M48 146 Q34 158 32 174', 32, 176),
    waveL: () => limb('M50 136 Q30 116 28 92', 28, 88),
    hipsL: () => limb('M48 146 Q24 150 34 172', 36, 174),
    headL: () => limb('M50 132 Q32 96 62 72', 64, 70),
  };
  const ARMR = {
    down:  () => limb('M192 146 Q206 158 208 174', 208, 176),
    waveR: () => limb('M190 136 Q210 116 212 92', 212, 88),
    holdR: () => limb('M190 138 Q214 122 216 98', 216, 94),
    pointR:() => limb('M192 146 Q214 142 232 140', 234, 140) + `<path d="M238 139 L252 137" stroke="${N}" stroke-width="7" stroke-linecap="round"/>`,
    cupR:  () => limb('M190 134 Q214 108 196 80', 194, 78),
    chin:  () => '',
    lensR: () => limb('M194 152 Q212 176 196 198', 194, 200),
    shh:   () => '',
  };
  const [aL, aR] = P.arms || ['down', 'down'];
  const armsSvg = (ARM[aL] || ARM.down)() + (ARMR[aR] || ARMR.down)();

  // --- глаза ---
  const eye = (x, i) => {
    const [dx, dy] = look; const cx = x + dx, cy = EY + dy;
    const two = (r1, r2, ox = -4.5, oy = -6.5) => `<circle cx="${cx + ox}" cy="${cy + oy}" r="${r1}" fill="#fff"/><circle cx="${cx + 5}" cy="${cy + 6}" r="${r2}" fill="#fff" opacity=".9"/>`;
    switch (P.eyes === 'lens' && i === 1 ? 'big' : P.eyes === 'lens' ? 'open' : P.eyes) {
      case 'open': return `<ellipse cx="${cx}" cy="${cy}" rx="14.5" ry="17.5" fill="${N}"/>${two(5.5, 2.4)}`;
      case 'big': return `<ellipse cx="${cx}" cy="${cy}" rx="18" ry="21.5" fill="${N}"/>${two(7, 3.2, -6, -8)}`;
      case 'wide': return `<ellipse cx="${cx}" cy="${cy - 2}" rx="16.5" ry="20.5" fill="${N}"/>${two(6.8, 3, -5, -10)}`;
      case 'up': return `<ellipse cx="${x}" cy="${EY}" rx="14.5" ry="17.5" fill="${N}"/><circle cx="${x + 1 + dx / 2}" cy="${EY - 9}" r="5.8" fill="#fff"/><circle cx="${x - 5}" cy="${EY + 7}" r="2.2" fill="#fff" opacity=".85"/>`;
      case 'half': {
        const L = x < 120;
        return `<path d="M${x - 14} ${EY + 3} A14 13 0 0 0 ${x + 14} ${EY + 3} Z" fill="${N}"/>
        <path d="M${x - 16} ${EY + (L ? 5 : 0)} L${x + 16} ${EY + (L ? 0 : 5)}" stroke="${N}" stroke-width="4.5" stroke-linecap="round"/>
        <circle cx="${x - 5 + dx}" cy="${EY + 8}" r="3" fill="#fff"/><circle cx="${x + 5 + dx}" cy="${EY + 11}" r="1.6" fill="#fff"/>`;
      }
      case 'closed': return `<path d="M${x - 13} ${EY} Q${x} ${EY + 11} ${x + 13} ${EY}" fill="none" stroke="${N}" stroke-width="5" stroke-linecap="round"/>`;
      case 'happy': return `<path d="M${x - 13} ${EY + 5} Q${x} ${EY - 14} ${x + 13} ${EY + 5}" fill="none" stroke="${N}" stroke-width="6" stroke-linecap="round"/>`;
    }
    return '';
  };
  const BROWS = {
    up: `<path d="M84 117 Q97 109 109 116" /><path d="M131 116 Q143 109 156 117"/>`,
    focus: `<path d="M84 124 L108 128"/><path d="M132 128 L156 124"/>`,
    sad: `<path d="M86 128 L107 121"/><path d="M133 121 L154 128"/>`,
  };
  const brows = P.brows ? `<g fill="none" stroke="${N}" stroke-width="4.5" stroke-linecap="round">${BROWS[P.brows]}</g>` : '';

  const MOUTH = {
    smile: `<path d="M110 ${MY - 3} Q120 ${MY + 7} 130 ${MY - 3}" fill="none" stroke="${N}" stroke-width="4.5" stroke-linecap="round"/>`,
    o: `<ellipse cx="120" cy="${MY + 1}" rx="7.5" ry="9" fill="${N}"/><ellipse cx="120" cy="${MY + 5}" rx="4.2" ry="3" fill="${K.blush}"/>`,
    small: `<ellipse cx="120" cy="${MY}" rx="4" ry="4.5" fill="${N}"/>`,
    flat: `<path d="M112 ${MY} Q116 ${MY - 3} 120 ${MY} T128 ${MY}" fill="none" stroke="${N}" stroke-width="4" stroke-linecap="round"/>`,
    line: `<path d="M113 ${MY} L127 ${MY}" stroke="${N}" stroke-width="4" stroke-linecap="round"/>`,
    grin: `<path d="M105 ${MY - 6} Q120 ${MY - 2} 135 ${MY - 6} Q132 ${MY + 14} 120 ${MY + 14} Q108 ${MY + 14} 105 ${MY - 6} Z" fill="${N}"/><path d="M111 ${MY + 7} Q120 ${MY + 1} 129 ${MY + 7} Q125 ${MY + 13} 120 ${MY + 13} Q115 ${MY + 13} 111 ${MY + 7} Z" fill="${K.blush}"/>`,
    shh: `<path d="M107 ${MY - 1} Q113 ${MY - 4} 120 ${MY - 1}" fill="none" stroke="${N}" stroke-width="4" stroke-linecap="round"/>`,
    hmm: `<path d="M111 ${MY + 1} Q121 ${MY - 4} 129 ${MY - 1}" fill="none" stroke="${N}" stroke-width="4" stroke-linecap="round"/>`,
    wobble: `<path d="M108 ${MY + 2} Q112 ${MY - 4} 116 ${MY + 2} T124 ${MY + 2} T132 ${MY + 2}" fill="none" stroke="${N}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>`,
  };

  // --- тело-яйцо ---
  const EGG = 'M120 40 C182 40 200 104 200 148 C200 192 166 216 120 216 C74 216 40 192 40 148 C40 104 58 40 120 40 Z';
  const HALF_L = 'M120 40 C58 40 40 104 40 148 C40 192 74 216 120 216 Z';
  const HALF_R = 'M120 40 C182 40 200 104 200 148 C200 192 166 216 120 216 Z';
  const ang = P.open ? 15 : 0;
  const spots = small ? '' : `<g fill="${B.spot}" opacity=".45"><circle cx="160" cy="86" r="7"/><circle cx="180" cy="114" r="5"/><circle cx="60" cy="124" r="5"/></g>`;
  const shine = small ? '' : `<ellipse cx="82" cy="80" rx="22" ry="10.5" transform="rotate(-38 82 80)" fill="#fff" opacity=".55"/>`;
  const membr = P.open ? `<g fill="${K.mintL}" opacity=".9">
      <ellipse cx="40" cy="64" rx="19" ry="50" transform="rotate(-50 40 64)"/><ellipse cx="200" cy="64" rx="19" ry="50" transform="rotate(50 200 64)"/></g>
      <g fill="none" stroke="${K.mint}" stroke-width="2" stroke-linecap="round" opacity=".8"><path d="M84 92 Q46 76 8 44"/><path d="M156 92 Q194 76 232 44"/></g>` : '';
  const elytra = P.open
    ? `<path d="${EGG}" fill="${B.under}"/>
       <g transform="rotate(${ang} 116 44)"><path d="${HALF_L}" fill="url(#${id}sh)"/></g>
       <g transform="rotate(${-ang} 124 44)"><path d="${HALF_R}" fill="url(#${id}sh)"/></g>`
    : `<path d="${EGG}" fill="url(#${id}sh)"/>
       <path d="M120 50 L120 104" stroke="${B.s2}" stroke-width="3" stroke-linecap="round" opacity=".7"/>`;

  // --- маячок ---
  const lit = P.beacon !== 'offline' && beacon !== K.grey;
  const beaconSvg = `<ellipse cx="120" cy="44" rx="14" ry="6" fill="${N}"/>
    <path d="M109 44 A11 12 0 0 1 131 44 Z" fill="${beacon}"/>
    ${small ? '' : `<ellipse cx="116" cy="37.5" rx="3.2" ry="2.4" fill="#fff" opacity=".8"/>`}
    ${lit && P.fx === 'flash' ? `<g stroke="${beacon}" stroke-width="4" stroke-linecap="round"><path d="M120 20 L120 12"/><path d="M104 26 L98 20"/><path d="M136 26 L142 20"/></g>` : ''}`;

  // --- предметы и сцены ---
  const PROP = {
    lens: `<path d="M190 198 L163 170" stroke="${N}" stroke-width="8" stroke-linecap="round"/>
      <circle cx="${EX[1]}" cy="${EY}" r="27" fill="${K.mintL}" opacity=".35"/>
      <circle cx="${EX[1]}" cy="${EY}" r="27" fill="none" stroke="${N}" stroke-width="6"/>
      <path d="M128 136 Q133 129 141 127" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round" opacity=".9"/>`,
    plug: `<path d="M216 104 Q224 140 240 150 Q256 160 252 184" fill="none" stroke="${N}" stroke-width="6" stroke-linecap="round"/>
      <rect x="203" y="58" width="26" height="36" rx="8" fill="${N}"/>
      <rect x="208" y="44" width="16" height="17" rx="3" fill="${K.grey}"/><rect x="212" y="48" width="8" height="4" rx="1.5" fill="${K.ink2}"/>
      <path d="M210 66 L210 84" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".35"/>`,
    medal: `<path d="M206 92 L214 128 L222 128 L214 92 Z" fill="${K.lilac}"/><path d="M226 92 L218 128 L210 128 L218 92 Z" fill="${K.link}"/>
      <circle cx="216" cy="94" r="9" fill="${K.ink}"/>
      <circle cx="216" cy="144" r="22" fill="${K.yellowD}"/><circle cx="216" cy="142" r="19" fill="${K.yellow}"/>
      <path d="M216 129 l4 8.5 9 1 -6.8 6 2 9 -8.2-4.6 -8.2 4.6 2-9 -6.8-6 9-1 Z" fill="${K.yellowD}"/>`,
  };
  const SCENE_BACK = {
    // дверь: проём #2B2645 в раме #7A6FF0; полотно #FFFDF8 открыто наружу вправо (трапеция, r4), торец #ECE9FF, ручка #FFD23F
    door: `<g>
      <rect x="148" y="-6" width="90" height="232" rx="4" fill="${K.lilac}"/>
      <rect x="157" y="3" width="72" height="223" rx="3" fill="${N}"/>
      <path d="M229 3 L272 -14 L272 240 L229 226 Z" fill="${K.card}" stroke="${K.card}" stroke-width="6" stroke-linejoin="round"/>
      <path d="M275 -15 L283 -12 L283 238 L275 241 Z" fill="${K.lilacL}" stroke="${K.lilacL}" stroke-width="3" stroke-linejoin="round"/>
      <rect x="252" y="110" width="7" height="22" rx="3.5" fill="${K.yellowD}"/><circle cx="255.5" cy="118" r="7" fill="${K.yellow}"/>
    </g>`,
    shelf: '',
  };
  const SCENE_FRONT = {
    shelf: `<rect x="6" y="220" width="228" height="16" rx="6" fill="${K.yellow}"/><rect x="6" y="230" width="228" height="6" rx="3" fill="${K.yellowD}"/>
      <path d="M40 236 L40 262 L66 236" fill="${K.yellowD}"/><path d="M200 236 L200 262 L174 236" fill="${K.yellowD}"/>`,
    door: '',
  };

  // --- спецэффекты ---
  const FX = {
    waves: `<g fill="none" stroke="${K.mint}" stroke-width="5" stroke-linecap="round" opacity=".8"><path d="M48 20 Q38 36 46 54"/><path d="M34 12 Q18 36 30 62"/><path d="M192 20 Q202 36 194 54"/><path d="M206 12 Q222 36 210 62"/></g>`,
    dotwaves: `<g fill="none" stroke="${K.mint}" stroke-width="5" stroke-linecap="round" stroke-dasharray="1 10"><path d="M190 10 Q214 34 210 62"/><path d="M206 -4 Q236 30 228 70"/></g>`,
    flash: `<g stroke="${K.tomato}" stroke-width="6" stroke-linecap="round"><path d="M70 -2 L60 -18"/><path d="M52 12 L36 4"/><path d="M170 -2 L180 -18"/><path d="M188 12 L204 4"/></g>`,
    dots: `<g><rect x="178" y="-6" width="58" height="32" rx="16" fill="${K.card}"/><path d="M188 22 L182 36 L200 25 Z" fill="${K.card}"/>
      <g fill="${K.lilac}"><circle cx="194" cy="10" r="4"/><circle cx="207" cy="10" r="4"/><circle cx="220" cy="10" r="4"/></g></g>`,
    nowifi: `<g transform="translate(206 8)"><circle r="22" fill="${K.card}"/><g fill="none" stroke="${K.grey}" stroke-width="4" stroke-linecap="round">
      <path d="M-11 -2 Q0 -12 11 -2"/><path d="M-6 4 Q0 -1 6 4"/></g><circle cx="0" cy="10" r="2.6" fill="${K.grey}"/>
      <path d="M-12 -12 L12 12" stroke="${K.ink2}" stroke-width="4" stroke-linecap="round"/></g>`,
    confetti: `<g><rect x="16" y="30" width="13" height="7" rx="3" fill="${K.tomato}" transform="rotate(-25 22 34)"/><rect x="214" y="28" width="13" height="7" rx="3" fill="${K.lilac}" transform="rotate(30 220 32)"/>
      <circle cx="40" cy="-6" r="5" fill="${K.yellow}"/><circle cx="200" cy="-8" r="5" fill="${K.mint}"/><circle cx="10" cy="80" r="4" fill="${K.blush}"/>
      <rect x="226" y="80" width="11" height="6" rx="3" fill="${K.yellow}" transform="rotate(50 231 83)"/></g>`,
    drop: `<path d="M180 66 Q190 82 190 90 A10 10 0 0 1 170 90 Q170 82 180 66 Z" fill="${K.lilac}" opacity=".75"/><ellipse cx="176" cy="88" rx="2.5" ry="4" fill="#fff"/>`,
  };

  const shhFront = aR === 'shh' ? `<path d="M182 198 Q162 212 142 196" fill="none" stroke="${N}" stroke-width="11" stroke-linecap="round"/>
      <path d="M131 190 L130 170" stroke="${N}" stroke-width="7.5" stroke-linecap="round"/><circle cx="134" cy="193" r="9.5" fill="${N}"/>` : '';
  const chinFront = aR === 'chin' ? `<path d="M184 196 Q170 210 152 202" fill="none" stroke="${N}" stroke-width="11" stroke-linecap="round"/><circle cx="148" cy="199" r="9.5" fill="${N}"/>` : '';

  const vb = P.scene === 'door' ? '-14 -28 306 306' : P.scene ? '-10 -20 270 290' : '-16 -24 272 272';
  const blush = P.blush === false ? '' : `<ellipse cx="73" cy="174" rx="10.5" ry="6.5" fill="${K.blush}" opacity=".65"/><ellipse cx="167" cy="174" rx="10.5" ry="6.5" fill="${K.blush}" opacity=".65"/>`;

  return `<svg class="bug" viewBox="${vb}" width="${size}" height="${size}" aria-hidden="true">
<defs>
 <radialGradient id="${id}sh" gradientUnits="userSpaceOnUse" cx="86" cy="76" r="168"><stop offset="0" stop-color="${B.s0}"/><stop offset=".38" stop-color="${B.s1}"/><stop offset="1" stop-color="${B.s2}"/></radialGradient>
 <radialGradient id="${id}bd" cx="35%" cy="30%" r="75%"><stop offset=".2" stop-color="${P.body === 'grey' ? K.greyL : K.yellow}"/><stop offset="1" stop-color="${P.body === 'grey' ? K.grey : K.yellowD}"/></radialGradient>
 <linearGradient id="${id}fc" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${K.card}"/><stop offset="1" stop-color="${K.bg}"/></linearGradient>
</defs>
${SCENE_BACK[P.scene] || ''}
<ellipse cx="120" cy="${P.scene === 'shelf' ? 220 : 222}" rx="76" ry="9" fill="${N}" opacity=".12"/>
${membr}
<path d="M58 192 Q44 204 38 218" fill="none" stroke="${N}" stroke-width="10" stroke-linecap="round"/>
<path d="M182 192 Q196 204 202 218" fill="none" stroke="${N}" stroke-width="10" stroke-linecap="round"/>
${stalks}
<ellipse cx="95" cy="216" rx="16" ry="9" fill="${N}"/><ellipse cx="145" cy="216" rx="16" ry="9" fill="${N}"/>
${P.open ? '' : armsSvg}
${elytra}
${P.open ? armsSvg : ''}
${P.open ? '' : spots + shine}
${P.open ? `<g transform="rotate(${ang} 116 44)">${small ? '' : `<ellipse cx="82" cy="80" rx="22" ry="10.5" transform="rotate(-38 82 80)" fill="#fff" opacity=".5"/>`}</g>` : ''}
<ellipse cx="120" cy="157" rx="65" ry="55" fill="${B.s2}" opacity=".35"/>
<ellipse cx="120" cy="159" rx="63" ry="53" fill="url(#${id}fc)"/>
${blush}${brows}${eye(EX[0], 0)}${eye(EX[1], 1)}${MOUTH[P.mouth || 'smile']}
${beaconSvg}${beads}
${P.prop ? PROP[P.prop] : ''}${shhFront}${chinFront}
${SCENE_FRONT[P.scene] || ''}
${P.fx && !small ? FX[P.fx] : ''}
</svg>`;
}
