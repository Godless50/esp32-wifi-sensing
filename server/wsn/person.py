"""Live position of one person on the plan.

Grid Bayes filter over the floor (cells 0.25 m inside the plan rooms; walls block movement, doors and arches let
it through). Evidence:
  * zone answers of the models (on-board and server learner), message type "pred";
  * which radio links "transmitter -> board" are disturbed (message type "motion"): a moving person disturbs a link
    most near its line of sight (first Fresnel zone) and somewhat anywhere in the rooms of its ends.

Noise rejection:
  * a model answer is dropped when stale (> PRED_MAX_AGE_S) or computed from too few live links; answers on
    overlapping windows are weighted by the time they add (dt / 1 s), and every probability has a floor, so one
    confident wrong answer cannot move the person;
  * a link counts as disturbed only above its own noise floor (10th percentile over 5 minutes), smoothed over
    LINK_EMA_S; links are used only while the person moves (a still person does not disturb them);
  * the person moves at most ~1.2 m/s and only through openings;
  * the shown zone changes only when the new zone holds > SWITCH_P of the belief for SWITCH_S; moving / still has
    hysteresis.
Only one person is tracked on the map. A still person is remembered where they stopped (the belief is frozen while
nobody moves) for as long as they are in the apartment: they count as gone only when their last movement ended at
the entrance door (an opening on an outer wall) and nothing has moved for LEAVE_S since. The state survives a
restart of the server (state_path). In addition, a lower bound on the number of people: links strongly
disturbed at the same time (> COUNT_ON x their noise floor for COUNT_HOLD_S) that no single room can explain need
at least as many people as the smallest set of rooms that explains them all ("min_people"; two people close to each
other still look like one). A room explains a link when a person there disturbs it (sensitivity >= EXPLAIN_S).
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import deque
from itertools import combinations
from pathlib import Path

import numpy as np

from .geometry import point_in_polygon

CELL_M = 0.25
OPENING_TYPES = ("дверь", "арка")
OPENING_MARGIN_M = 0.15

PRED_MAX_AGE_S = 2.0
MIN_LINKS_LIVE = 4
P_FLOOR = 0.05
# Зоны: если в ответе есть counts_probs (многоголовая модель, все комнаты плана) — свидетельство «в зоне кто-то
# двигается» по каждой комнате. Иначе (модель на 4 зоны) комнаты, которых она не знает, получают нейтральное 1/K.
SRC_WEIGHT = {"board": 0.8, "server": 0.8}
STILL_MODEL_W = 0.4
SPEED_MOVING, SPEED_STILL = 1.5, 0.1  # м/с: разброс веры за секунду
SELF_W = 0.5
REACQUIRE_PER_S = 0.002  # доля равномерной веры: фильтр может «найти» человека заново после ошибки
MAX_DIFFUSE_STEPS = 60

FLOOR_WINDOW_S, FLOOR_Q, FLOOR_MIN_N, FLOOR_EVERY_S = 300.0, 10.0, 40, 5.0
LINK_EMA_S = 0.75
LINK_ON, LINK_SPAN = 1.5, 1.5  # отношение к шумовому полу: 1.5 -> 0, 3.0 -> 1
FRESNEL_M = 0.6
SAME_ROOM_S = 0.3
LINK_W = 1.5  # линии (геометрия) — главный источник для комнат, где модель не видела движения
LINK_MAX_AGE_S = 2.0
QUIET_LINK = 0.0  # тихая линия ничего не говорит о месте: неподвижный человек линии не возмущает

MOVE_EMA_S = 1.0
MOVE_ON, MOVE_OFF, MOVE_ON_S, MOVE_OFF_S = 0.6, 0.35, 0.5, 3.0
SWITCH_P, SWITCH_S = 0.5, 0.5  # смена зоны на экране: быстрее, но не от одного ответа
LEAVE_S = 180.0  # неподвижность у входной двери дольше этого — человек вышел
EXIT_NEAR_M = 1.5
SAVE_EVERY_S = 30.0
BREATH_FRESH_S = 60.0
XY_EMA_S = 0.2
EMIT_PERIOD_S, HEAT_PERIOD_S, HEAT_MAX = 0.25, 1.0, 80
COUNT_ON, COUNT_HOLD_S, EXPLAIN_S = 3.0, 2.0, 0.25
# Комнаты (сообщение "rooms" раз в секунду): движение по каждой комнате плана.
#   плата в комнате: человек рядом с приёмником возмущает почти все его линии — медиана их активности;
#   остальные комнаты: общая оценка движения x доля веры трекера в этой комнате (примерно).
ROOM_EMIT_S, ROOM_HIST_S, ROOM_HIST_STEP = 1.0, 600.0, 5.0
ROOM_MOTION_ON = 0.35  # с этого уровня в комнате «движение»
LOCAL_MIN_LINKS = 3
# Комната со своей платой: средняя активность линий платы (EMA BOARD_EMA_S). Человек рядом с приёмником возмущает
# его линии сильнее, чем линии других плат; движение в другом месте задевает все платы понемногу. Поэтому
# «кто-то здесь» = активность платы >= LOCAL_ON и выше медианы по всем платам на LOCAL_ABOVE (13.10, человек сидит
# в туалете: попадание 0.77, ложные в пустых комнатах с платами 0.01; прежний порог по медиане линий — 0.04).
BOARD_EMA_S, LOCAL_ON, LOCAL_ABOVE = 3.0, 0.05, 0.02
LOCAL_W = 1.0  # то же как свидетельство о месте для трекера: клетки этой комнаты x2 за секунду
EXTRA_EMA_S = 3.0  # сглаживание числа людей и позы от многоголовой модели
EXTRA_FRESH_S = 5.0


def plan_key(plan: dict) -> str:
    part = {k: plan.get(k) for k in ("rooms", "zones", "objects", "board", "boards", "router")}
    return hashlib.sha1(json.dumps(part, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Grid:
    """Floor cells of the plan, their room / zone labels, moves allowed between cells, link sensitivities."""

    def __init__(self, plan: dict):
        rooms = [(r["name"], [tuple(map(float, p)) for p in r["polygon"]]) for r in plan.get("rooms") or []]
        zones = [(z["name"], [tuple(map(float, p)) for p in z["polygon"]]) for z in plan.get("zones") or []]
        if not rooms:
            raise ValueError("plan has no rooms")
        pts = [p for _, poly in rooms for p in poly]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        index: dict[tuple[int, int], int] = {}
        xy, room_of, label = [], [], []
        for iy, y in enumerate(np.arange(y0 + CELL_M / 2, y1, CELL_M)):
            for ix, x in enumerate(np.arange(x0 + CELL_M / 2, x1, CELL_M)):
                room = next((n for n, poly in rooms if point_in_polygon((x, y), poly)), None)
                if room is None:
                    continue
                zone = next((n for n, poly in zones if point_in_polygon((x, y), poly)), room)
                index[(ix, iy)] = len(xy)
                xy.append((float(x), float(y)))
                room_of.append(room)
                label.append(zone)
        if not xy:
            raise ValueError("plan rooms contain no cells")
        self.xy = np.array(xy)
        self.n = len(xy)
        self.room = room_of
        self.labels = sorted(set(label))
        self.label_idx = np.array([self.labels.index(z) for z in label])
        openings = [(np.array(o["xy"], float), float((o.get("size") or [0.8])[0]) / 2 + OPENING_MARGIN_M)
                    for o in plan.get("objects") or [] if o.get("type") in OPENING_TYPES]
        src, dst = [], []
        for (ix, iy), i in index.items():
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                j = index.get((ix + dx, iy + dy))
                if j is None:
                    continue
                if room_of[i] != room_of[j]:  # между комнатами — только через дверь или арку
                    mid = (self.xy[i] + self.xy[j]) / 2
                    if not any(np.hypot(*(mid - c)) < r for c, r in openings):
                        continue
                src += [i, j]
                dst += [j, i]
        self.src = np.array(src, dtype=int)
        self.dst = np.array(dst, dtype=int)
        deg = np.bincount(self.src, minlength=self.n) if len(src) else np.zeros(self.n, int)
        self.self_w = np.where(deg > 0, SELF_W, 1.0)
        self.w_edge = (1 - SELF_W) / np.maximum(deg, 1)[self.src] if len(src) else np.zeros(0)
        # передатчики и приёмники
        self.tx = {o["mac"].lower(): np.array(o["xy"], float) for o in plan.get("objects") or [] if o.get("mac")}
        self.router = np.array(plan["router"], float) if plan.get("router") else None
        boards = {str(b["device_id"]).lower(): np.array(b["xy"], float) for b in plan.get("boards") or []}
        self.boards = boards
        # модели (зоны, люди) обучены на линиях первой платы плана: ответы других плат о зонах не принимаем,
        # их линии идут только в геометрию
        self.model_board = str(plan["boards"][0]["device_id"]).lower() if plan.get("boards") else None
        self.default_rx = np.array(plan.get("board") or [0.0, 0.0], float) if not boards else None
        # входная дверь: проём, рядом с которым клетки только одной комнаты (на внешней стене)
        self.exits = []
        for c, r in openings:
            near = {room_of[i] for i in np.flatnonzero(np.hypot(*(self.xy - c).T) < r + 0.3)}
            if len(near) == 1:
                self.exits.append(c)
        self._sens: dict[tuple[str, str], np.ndarray | None] = {}
        self._explains: dict[tuple[str, str], frozenset] = {}
        self.rooms = sorted(set(room_of))
        self._room_mask = {r: np.array([x == r for x in room_of]) for r in self.rooms}

    def room_at(self, p: np.ndarray) -> str:
        return self.room[int(np.argmin(np.hypot(*(self.xy - p).T)))]

    def diffuse(self, b: np.ndarray, steps: int) -> np.ndarray:
        for _ in range(steps):
            nb = b * self.self_w
            if len(self.src):
                nb += np.bincount(self.dst, weights=b[self.src] * self.w_edge, minlength=self.n)
            b = nb
        return b

    def sensitivity(self, device_id: str, src: str, primary: bool) -> np.ndarray | None:
        """How much a person in each cell disturbs the link (0..1); None when an end of the link is not on the plan."""
        key = (device_id, src)
        if key in self._sens:
            return self._sens[key]
        tx = self.tx.get(src)
        if tx is None and primary:
            tx = self.router
        rx = self.boards.get(device_id, self.default_rx)
        s = None
        if tx is not None and rx is not None:
            d = np.hypot(*(self.xy - tx).T) + np.hypot(*(self.xy - rx).T) - float(np.hypot(*(tx - rx)))
            ends = {self.room_at(tx), self.room_at(rx)}
            same = np.array([r in ends for r in self.room], dtype=float)
            s = np.maximum(np.exp(-d / FRESNEL_M), SAME_ROOM_S * same)
        self._sens[key] = s
        return s


    def explaining_rooms(self, device_id: str, src: str, primary: bool) -> frozenset:
        """Rooms where one person disturbs this link."""
        key = (device_id, src)
        if key not in self._explains:
            s = self.sensitivity(device_id, src, primary)
            self._explains[key] = frozenset() if s is None else frozenset(
                r for r in self.rooms if float(s[self._room_mask[r]].max()) >= EXPLAIN_S)
        return self._explains[key]


def min_cover(sets: list[frozenset], rooms: list[str]) -> tuple[int, list[str]]:
    """Smallest number of rooms that together explain every link (each set = rooms explaining one link)."""
    sets = [x for x in sets if x]
    if not sets:
        return 0, []
    for k in range(1, len(rooms) + 1):
        for combo in combinations(rooms, k):
            c = set(combo)
            if all(x & c for x in sets):
                return k, list(combo)
    return len(sets), []


class _Link:
    def __init__(self):
        self.raw: deque = deque()  # (t, raw)
        self.floor: float | None = None
        self.floor_at = -1e9
        self.ratio = 0.0
        self.t = -1e9
        self.primary = False
        self.on_since = None  # с какого момента линия сильно возмущена (для оценки «не меньше N»)


class PersonTracker:
    def __init__(self, publish=None, state_path: str | Path | None = None):
        self.publish = publish
        self.state_path = Path(state_path) if state_path else None
        self._saved_at = -1e9
        self._restore: dict | None = None
        self.left = False  # ушёл через входную дверь
        self._stop_near_exit = False
        self._lock = threading.Lock()
        self.grid: Grid | None = None
        self._plan_key = None
        self.belief: np.ndarray | None = None
        self.geo: np.ndarray | None = None  # вера только по линиям (физика, без нейросети) — учитель для дообучения
        self._links: dict[tuple[str, str], _Link] = {}
        self._last_pred: dict[tuple[str, str], float] = {}
        self._src_t: dict[str, float] = {}
        self._mv: dict[tuple[str, str], tuple[float, float]] = {}  # источник -> (t, EMA moving_p)
        self._act: dict[str, float] = {}
        self._act_t = -1e9
        self._t = None  # время последнего шага фильтра
        self._steps_acc = 0.0
        self.moving = False
        self._move_cand_since = None
        self.last_moving_t = None
        self.shown = None
        self._cand = None
        self._cand_since = 0.0
        self.xy = None
        self._last_emit = -1e9
        self._last_heat = -1e9
        self._breath = None  # (t, bpm)
        self._extra: dict[str, tuple[float, object]] = {}  # поле -> (t, сглаженное значение)
        self._score = 0.0  # общая оценка «кто-то движется» 0..1
        self._room_hist: dict[str, deque] = {}
        self._room_last_motion: dict[str, float] = {}
        self._link_breath: dict[tuple[str, str], tuple[float, float]] = {}  # линия -> (t, вдохов/мин)
        self._rooms_at = -1e9
        self._board_act: dict[str, tuple[float, float]] = {}  # плата -> (t, EMA средней активности её линий)
        self.last: dict | None = None

    # ---- план ----
    def set_plan(self, plan: dict) -> bool:
        """Rebuild the grid when the plan changed. Returns True when rebuilt."""
        key = plan_key(plan)
        if key == self._plan_key:
            return False
        try:
            grid = Grid(plan)
        except (ValueError, KeyError, TypeError):
            grid = None
        with self._lock:
            self._plan_key = key
            self.grid = grid
            self.belief = np.full(grid.n, 1.0 / grid.n) if grid else None
            self.geo = None if grid is None else self.belief.copy()
            self.shown = self._cand = self.xy = None
            if grid is not None:
                self._load_state(key)
        return True

    # ---- сохранение между перезапусками ----
    def _load_state(self, key: str) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            st = json.loads(self.state_path.read_text())
            if st.get("plan") != key or len(st.get("belief", [])) != self.grid.n:
                return
            b = np.array(st["belief"], float)
            self.belief = b / b.sum() if b.sum() > 0 else self.belief
            self.shown = st.get("shown")
            self.xy = np.array(st["xy"], float) if st.get("xy") else None
            self.left = bool(st.get("left"))
            self._stop_near_exit = bool(st.get("stop_near_exit"))
            still = st.get("still_since_wall")
            if still is None and st.get("seen"):
                still = st.get("saved_wall")  # двигался при сохранении: неподвижен с того момента
            self._restore = {"still_wall": still}
        except (ValueError, KeyError, TypeError, OSError):
            pass

    def _save_state(self, now: float) -> None:
        if self.state_path is None or now - self._saved_at < SAVE_EVERY_S or self.grid is None:
            return
        self._saved_at = now
        still_wall = None
        if self.last_moving_t is not None and not self.moving:
            still_wall = time.time() - (now - self.last_moving_t)
        st = {"plan": self._plan_key, "belief": [round(float(v), 7) for v in self.belief], "shown": self.shown,
              "xy": None if self.xy is None else [float(v) for v in self.xy], "left": self.left,
              "stop_near_exit": self._stop_near_exit, "still_since_wall": still_wall,
              "seen": self.last_moving_t is not None, "saved_wall": time.time()}
        try:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st))
            tmp.replace(self.state_path)
        except OSError:
            pass

    # ---- входящие сообщения хаба ----
    def on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "motion":
            with self._lock:
                self._on_motion(msg)
        elif kind == "breath":
            if msg.get("ok"):
                with self._lock:
                    self._breath = (float(msg["t"]), float(msg["bpm"]))
                    key = (str(msg.get("device_id", "")).lower(), str(msg.get("src", "")).lower())
                    self._link_breath[key] = (float(msg["t"]), float(msg["bpm"]))
        elif kind == "pred":
            with self._lock:
                out = self._on_pred(msg)
                rooms = self._room_states(self._t) if self.grid is not None and self._t is not None else None
            if self.publish is not None:
                for m in (out, rooms):
                    if m is not None:
                        self.publish(m)

    def _on_motion(self, msg: dict) -> None:
        key = (str(msg.get("device_id", "")).lower(), str(msg.get("src", "")).lower())
        raw = msg.get("raw")
        if raw is None:
            return
        t = float(msg["t"])
        ln = self._links.get(key)
        if ln is None:
            ln = self._links[key] = _Link()
        ln.primary = bool(msg.get("primary"))
        ln.raw.append((t, float(raw)))
        while ln.raw and ln.raw[0][0] < t - FLOOR_WINDOW_S:
            ln.raw.popleft()
        if len(ln.raw) >= FLOOR_MIN_N and t - ln.floor_at >= FLOOR_EVERY_S:
            ln.floor = float(np.percentile([r for _, r in ln.raw], FLOOR_Q))
            ln.floor_at = t
        if ln.floor is None or ln.floor <= 1e-9:
            return
        r = float(raw) / ln.floor
        a = 1.0 - math.exp(-max(0.0, t - ln.t) / LINK_EMA_S) if ln.t > -1e8 else 1.0
        ln.ratio += a * (r - ln.ratio)
        ln.t = t
        if ln.ratio >= COUNT_ON:
            ln.on_since = t if ln.on_since is None else ln.on_since
        else:
            ln.on_since = None

    def _link_activity(self, now: float) -> dict[tuple[str, str], float]:
        out = {}
        for key, ln in self._links.items():
            if ln.floor is None or now - ln.t > LINK_MAX_AGE_S:
                continue
            out[key] = min(1.0, max(0.0, (ln.ratio - LINK_ON) / LINK_SPAN))
        return out

    def _update_board_act(self, now: float) -> None:
        per: dict[str, list[float]] = {}
        for (dev, _), a in self._link_activity(now).items():
            per.setdefault(dev, []).append(a)
        for dev, xs in per.items():
            if len(xs) < LOCAL_MIN_LINKS:
                continue
            v = float(np.mean(xs))
            old = self._board_act.get(dev)
            if old is None or now - old[0] > 10.0:
                self._board_act[dev] = (now, v)
            else:
                a = 1.0 - math.exp(-max(0.0, now - old[0]) / BOARD_EMA_S)
                self._board_act[dev] = (now, old[1] + a * (v - old[1]))

    def _board_local(self, now: float) -> dict[str, tuple[float, bool]]:
        """Room of each board -> (activity of its links, somebody near this board)."""
        g = self.grid
        fresh = {d: v for d, (t, v) in self._board_act.items() if now - t <= LINK_MAX_AGE_S + 1.0 and d in g.boards}
        if not fresh:
            return {}
        med = float(np.median(list(fresh.values())))
        out: dict[str, tuple[float, bool]] = {}
        for dev, v in fresh.items():
            lab = g.labels[g.label_idx[int(np.argmin(np.hypot(*(g.xy - g.boards[dev]).T)))]]
            here = v >= LOCAL_ON and (len(fresh) < 2 or v - med >= LOCAL_ABOVE)
            pv, ph = out.get(lab, (0.0, False))
            out[lab] = (max(pv, v), ph or here)
        return out

    def _local_evidence(self, b: np.ndarray, now: float, w: float) -> np.ndarray:
        g = self.grid
        for lab, (_, here) in self._board_local(now).items():
            if here:
                b = b * np.where(g.label_idx == g.labels.index(lab), 1.0 + LOCAL_W, 1.0) ** w
        return b

    def _on_pred(self, msg: dict) -> dict | None:
        g = self.grid
        if g is None:
            return None
        t = float(msg["t"])
        now = max(t, self._t or t)
        self._smooth_extras(msg, t)
        if self._restore is not None:  # после перезапуска: человек неподвижен с того же момента
            w = self._restore.get("still_wall")
            if w is not None:
                self.last_moving_t = now - max(0.0, time.time() - float(w))
            self._restore = None
        source = str(msg.get("source", "server"))
        skey = (source, str(msg.get("device_id", "")).lower())
        if now - t > PRED_MAX_AGE_S:
            return None
        if msg.get("links_live") is not None and int(msg["links_live"]) < MIN_LINKS_LIVE:
            return None
        if g.model_board and str(msg.get("device_id", "")).lower() not in (g.model_board, ""):
            return None
        probs = msg.get("probs") or {}
        if not probs:
            return None
        dt_src = min(1.0, max(0.0, t - self._last_pred.get(skey, t - 1.0)))
        self._last_pred[skey] = t
        self._src_t[source] = t
        # движение: EMA по каждому источнику, потом среднее свежих + активность линий
        mt, mv = self._mv.get(skey, (t, float(msg.get("moving_p", 0.0))))
        a = 1.0 - math.exp(-max(0.0, t - mt) / MOVE_EMA_S) if skey in self._mv else 1.0
        mv += a * (float(msg.get("moving_p", 0.0)) - mv)
        self._mv[skey] = (t, mv)
        fresh = [v for (tt, v) in self._mv.values() if now - tt <= PRED_MAX_AGE_S]
        model_mv = sum(fresh) / len(fresh) if fresh else 0.0
        acts = self._link_activity(now)
        self._update_board_act(now)
        top = sorted(acts.values(), reverse=True)[:2]
        link_mv = sum(top) / len(top) if top else 0.0
        score = 0.75 * model_mv + 0.25 * link_mv if fresh else link_mv
        self._score = score
        self._update_moving(score, now)
        # активность (машет / ходит / ...): EMA вероятности класса
        if msg.get("activity"):
            a = 1.0 - math.exp(-max(0.0, now - self._act_t) / MOVE_EMA_S)
            for k in list(self._act):
                self._act[k] *= 1 - a
            self._act[msg["activity"]] = self._act.get(msg["activity"], 0.0) + a * float(msg.get("activity_p", 1.0))
            self._act_t = now
        # шаг фильтра: разброс по времени, затем свидетельства
        dt = 0.0 if self._t is None else max(0.0, now - self._t)
        self._t = now
        b = self.belief
        if not self.moving:  # неподвижен: помним, где остановился (ответы модели о зоне сейчас — шум)
            if g.boards:  # но мелкие движения рядом с платой видны: человек у этой платы
                lb = self._local_evidence(b, now, 0.5 * dt_src)
                if lb.sum() > 0 and np.isfinite(lb.sum()):
                    self.belief = lb / lb.sum()
            return self._state(now, dt)
        speed = SPEED_MOVING
        self._steps_acc += (speed / CELL_M) ** 2 * dt / 0.75
        steps = min(MAX_DIFFUSE_STEPS, int(self._steps_acc))
        self._steps_acc = min(self._steps_acc - steps, 1.0)
        b = g.diffuse(b, steps)
        geo = g.diffuse(self.geo, steps)
        cp = msg.get("counts_probs")
        if cp:  # P(в зоне двигается хотя бы один) — только по зонам, где у модели были настоящие примеры движения
            known = {z: 1.0 - float(v[0]) for z, v in cp.items() if z in probs}
            neutral = sum(known.values()) / len(known) if known else 0.5
            p_label = np.array([known.get(z, neutral) for z in g.labels])
        else:
            unseen = 1.0 / len(probs)
            p_label = np.array([float(probs.get(z, unseen)) for z in g.labels])
        w = SRC_WEIGHT.get(source, 0.5) * dt_src * (1.0 if self.moving else STILL_MODEL_W)
        b = b * (p_label[g.label_idx] + P_FLOOR) ** w
        if acts:
            lw = LINK_W * dt_src
            lf = np.ones(g.n)
            for (dev, src), act in acts.items():
                s = g.sensitivity(dev, src, self._links[(dev, src)].primary)
                if s is None:
                    continue
                # тихая линия — слабое свидетельство «не рядом» (иначе человек «уезжает» туда, где линий нет)
                lf = lf * ((1 - act) * (1 - QUIET_LINK * s) + act * (0.15 + 0.85 * s)) ** lw
            b = b * lf
            geo = geo * lf
        b = self._local_evidence(b, now, dt_src)
        rho = min(1.0, REACQUIRE_PER_S * dt)
        norm = lambda v: v / v.sum() if v.sum() > 0 and np.isfinite(v.sum()) else np.full(g.n, 1.0 / g.n)
        self.belief = (1 - rho) * norm(b) + rho / g.n
        self.geo = (1 - rho) * norm(geo) + rho / g.n
        return self._state(now, dt)

    def _smooth_extras(self, msg: dict, t: float) -> None:
        """EMA of the multi-head model outputs (counts, pose); dicts and lists of probabilities."""
        for k in ("people_probs", "counts_probs", "pose_probs", "arms_probs", "legs_probs", "angles"):
            v = msg.get(k)
            if v is None:
                continue
            if isinstance(v, dict):
                new = {kk: np.asarray(vv, float) for kk, vv in v.items()}
            else:
                new = np.asarray(v, float)
            old = self._extra.get(k)
            if old is None or t - old[0] > EXTRA_FRESH_S:
                self._extra[k] = (t, new)
                continue
            a = 1.0 - math.exp(-max(0.0, t - old[0]) / EXTRA_EMA_S)
            if isinstance(new, dict):
                val = {kk: old[1].get(kk, vv) + a * (vv - old[1].get(kk, vv)) for kk, vv in new.items()}
            else:
                val = old[1] + a * (new - old[1])
            self._extra[k] = (t, val)

    def _extras_out(self, now: float) -> dict:
        ex = {k: v for k, (tt, v) in self._extra.items() if now - tt <= EXTRA_FRESH_S}
        out = {}
        if "people_probs" in ex:
            p = ex["people_probs"]
            out["people"] = {"n": int(np.argmax(p)), "p": round(float(np.max(p)), 3), "of": "движущихся",
                             "probs": [round(float(x), 3) for x in p]}
        if "counts_probs" in ex:
            out["counts"] = {z: int(np.argmax(p)) for z, p in ex["counts_probs"].items() if int(np.argmax(p)) > 0}
        if "pose_probs" in ex and out.get("people", {}).get("n", 1) <= 1:  # поза — только когда человек один
            pose = {}
            for k, name in (("pose_probs", "pose"), ("arms_probs", "arms"), ("legs_probs", "legs")):
                if k in ex:
                    best = max(ex[k], key=lambda z: float(ex[k][z]))
                    pose[name] = best
                    pose[name + "_p"] = round(float(ex[k][best]), 3)
            if "angles" in ex:
                pose["angles"] = [round(float(x)) for x in ex["angles"]]
            out["pose"] = pose
        return out

    def _update_moving(self, score: float, now: float) -> None:
        want = score > MOVE_ON if not self.moving else score > MOVE_OFF
        if want != self.moving:
            if self._move_cand_since is None:
                self._move_cand_since = now
            if now - self._move_cand_since >= (MOVE_ON_S if want else MOVE_OFF_S):
                self.moving = want
                self._move_cand_since = None
                if not want:
                    self._stop_near_exit = self._near_exit()
        else:
            self._move_cand_since = None
        if self.moving:
            self.last_moving_t = now
            self.left = False

    def _near_exit(self) -> bool:
        g = self.grid
        return self.xy is not None and any(float(np.hypot(*(self.xy - c))) < EXIT_NEAR_M for c in g.exits)

    def _state(self, now: float, dt: float) -> dict | None:
        g, b = self.grid, self.belief
        masses = np.bincount(g.label_idx, weights=b, minlength=len(g.labels))
        best = int(np.argmax(masses))
        if self.shown is None:
            self.shown = best
        elif best != self.shown and masses[best] > SWITCH_P:
            if self._cand != best:
                self._cand, self._cand_since = best, now
            elif now - self._cand_since >= SWITCH_S:
                self.shown, self._cand = best, None
        else:
            self._cand = None
        inz = g.label_idx == self.shown
        wz = b * inz
        c = (wz @ g.xy) / wz.sum() if wz.sum() > 0 else g.xy[inz].mean(axis=0)
        cells = g.xy[inz]
        c = cells[int(np.argmin(np.hypot(*(cells - c).T)))]  # Г-образная зона: точка внутри зоны
        spread = float(np.sqrt((wz * ((g.xy - c) ** 2).sum(axis=1)).sum() / max(wz.sum(), 1e-12)))
        cur = None if self.xy is None else g.label_idx[int(np.argmin(np.hypot(*(g.xy - self.xy).T)))]
        if cur != self.shown:
            self.xy = c.astype(float)  # смена зоны: сразу в новую зону (интерфейс сам плавно переведёт)
        else:
            k = 1.0 - math.exp(-dt / XY_EMA_S)
            self.xy = self.xy + k * (c - self.xy)
        if now - self._last_emit < EMIT_PERIOD_S - 1e-9:
            return None
        self._last_emit = now
        still = 0.0 if self.moving or self.last_moving_t is None else now - self.last_moving_t
        breath = self._breath[1] if self._breath and now - self._breath[0] <= BREATH_FRESH_S else None
        if not self.moving and self._stop_near_exit and still > LEAVE_S and breath is None:
            self.left = True
        seen = self.last_moving_t is not None
        status = ("ждём движения" if not seen else "вышел" if self.left else "движется" if self.moving
                  else "неподвижен")
        act = max(self._act, key=self._act.get) if self._act else None
        strong = [(d, s_) for (d, s_), ln in self._links.items()
                  if ln.on_since is not None and now - ln.t <= LINK_MAX_AGE_S and ln.t - ln.on_since >= COUNT_HOLD_S]
        n_min, cover = min_cover([g.explaining_rooms(d, s_, self._links[(d, s_)].primary) for d, s_ in strong], g.rooms)
        if self.moving:
            n_min = max(n_min, 1)
        out = {
            "type": "person", "t": now, "moving": self.moving,
            "present": seen and not self.left, "status": status,
            "zone": g.labels[self.shown], "zone_p": round(float(masses[self.shown]), 3),
            "xy": [round(float(self.xy[0]), 2), round(float(self.xy[1]), 2)], "spread_m": round(spread, 2),
            "activity": (act if act and act != "нет движения" else "движется") if self.moving else "нет движения",
            "still_s": round(still, 1), "breath_bpm": breath,
            "zones": {z: round(float(m), 3) for z, m in zip(g.labels, masses) if m >= 0.01},
            "sources": {s: round(now - tt, 1) for s, tt in self._src_t.items()},
            "links_active": sum(1 for v in self._link_activity(now).values() if v > 0.3),
            "min_people": n_min, "min_rooms": cover if n_min >= 2 else [],
            **self._extras_out(now),
        }
        gm = np.bincount(g.label_idx, weights=self.geo, minlength=len(g.labels))
        out["geo"] = {"zone": g.labels[int(np.argmax(gm))], "p": round(float(gm.max()), 3)}
        if now - self._last_heat >= HEAT_PERIOD_S:
            self._last_heat = now
            idx = np.argsort(b)[::-1][:HEAT_MAX]
            idx = idx[b[idx] >= b[idx[0]] * 0.05]
            out["heat"] = [[round(float(g.xy[i, 0]), 2), round(float(g.xy[i, 1]), 2), round(float(b[i]), 4)]
                           for i in idx]
        self.last = out
        self._save_state(now)
        return out

    def _link_room(self, dev: str, src: str, primary: bool) -> str | None:
        """Room label of a link for breathing: where its transmitter stands (else the receiver)."""
        g = self.grid
        tx = g.tx.get(src)
        if tx is None and primary:
            tx = g.router
        p = tx if tx is not None else g.boards.get(dev, g.default_rx)
        if p is None:
            return None
        return g.labels[g.label_idx[int(np.argmin(np.hypot(*(g.xy - p).T)))]]

    def _room_states(self, now: float) -> dict | None:
        if now - self._rooms_at < ROOM_EMIT_S - 1e-9:
            return None
        self._rooms_at = now
        g = self.grid
        board_room = {dev: g.labels[g.label_idx[int(np.argmin(np.hypot(*(g.xy - p).T)))]]
                      for dev, p in g.boards.items()}
        local: dict[str, float] = {}
        for lab, (v, here) in self._board_local(now).items():
            # «кто-то здесь» -> не ниже «слабое», сильная активность -> «движение»; иначе ниже порога «слабое»
            local[lab] = min(1.0, 0.15 + 2.0 * (v - LOCAL_ON)) if here else min(0.14, 2.0 * v)
        mass = np.bincount(g.label_idx, weights=self.belief, minlength=len(g.labels))
        breath: dict[str, float] = {}
        for (dev, src), (tb, bpm) in self._link_breath.items():
            if now - tb <= BREATH_FRESH_S:
                ln = self._links.get((dev, src))
                lab = self._link_room(dev, src, bool(ln and ln.primary))
                if lab is not None:
                    breath[lab] = bpm
        rooms = []
        for i, lab in enumerate(g.labels):
            est = float(self._score) * float(mass[i]) if self.moving else 0.0
            m = max(local.get(lab, 0.0), est)
            if m >= ROOM_MOTION_ON:
                self._room_last_motion[lab] = now
            h = self._room_hist.setdefault(lab, deque())
            h.append((now, m))
            while h and h[0][0] < now - ROOM_HIST_S:
                h.popleft()
            bins = {}
            for t_, v in h:  # максимум за каждые ROOM_HIST_STEP секунд, от старых к новым
                k = int((now - t_) // ROOM_HIST_STEP)
                bins[k] = max(bins.get(k, 0.0), v)
            n_bins = int(ROOM_HIST_S // ROOM_HIST_STEP)
            hist = [round(bins.get(k, 0.0), 2) for k in range(n_bins - 1, -1, -1)]
            last = self._room_last_motion.get(lab)
            rooms.append({
                "name": lab, "motion": round(m, 2),
                "state": "движение" if m >= ROOM_MOTION_ON else "слабое" if m >= 0.15 else "тихо",
                "source": "board" if lab in local else "estimate",
                "quiet_s": None if last is None else round(now - last, 1),
                "breath_bpm": round(breath[lab]) if lab in breath else None,
                "hist": hist,
            })
        return {"type": "rooms", "t": now, "rooms": rooms, "boards": board_room}

    def reset(self) -> None:
        """Forget the position (wrong after a stuck estimate): uniform belief, waiting for movement again."""
        with self._lock:
            if self.grid is not None:
                self.belief = np.full(self.grid.n, 1.0 / self.grid.n)
                self.geo = self.belief.copy()
            self.shown = self._cand = self.xy = None
            self.last_moving_t = None
            self.left = False
            self._stop_near_exit = False
            self._saved_at = -1e9
            if self.state_path is not None and self.state_path.exists():
                self.state_path.unlink()

    def snapshot(self) -> dict:
        with self._lock:
            g = self.grid
            return {
                "person": self.last,
                "grid": None if g is None else {"cells": g.n, "cell_m": CELL_M, "labels": g.labels,
                                                 "moves": int(len(g.src) // 2)},
                "links": {f"{d}|{s}": {"floor": None if ln.floor is None else round(ln.floor, 4),
                                       "ratio": round(ln.ratio, 2), "age_s": round((self._t or 0) - ln.t, 1),
                                       "on_plan": g is not None and g.sensitivity(d, s, ln.primary) is not None}
                          for (d, s), ln in self._links.items()},
            }
