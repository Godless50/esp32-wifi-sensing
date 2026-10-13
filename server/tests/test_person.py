import numpy as np
import pytest

from wsn.live import LiveHub
from wsn.person import Grid, PersonTracker

# две комнаты 3 x 3 м, общая стена x = 3, дверь у y = 2.5
PLAN = {
    "rooms": [{"name": "A", "polygon": [[0, 0], [3, 0], [3, 3], [0, 3]]},
              {"name": "B", "polygon": [[3, 0], [6, 0], [6, 3], [3, 3]]}],
    "zones": [],
    "objects": [{"type": "дверь", "xy": [3.0, 2.5], "size": [0.8, 0.1, 2.0]},
                {"type": "дверь", "xy": [0.0, 1.5], "size": [0.8, 0.1, 2.0]},  # входная: на внешней стене A
                {"type": "датчик", "xy": [0.3, 0.5], "mac": "aa:00:00:00:00:01"},
                {"type": "датчик", "xy": [0.3, 2.5], "mac": "aa:00:00:00:00:02"}],
    "board": [0.0, 0.0],
    "boards": [{"device_id": "d1", "xy": [2.7, 0.5]}, {"device_id": "d2", "xy": [2.7, 2.5]}],
    "router": None,
}


def pred(t, pa, moving=0.9, source="board", links_live=8, dev="d1"):
    return {"type": "pred", "source": source, "device_id": dev, "t": t, "probs": {"A": pa, "B": 1 - pa},
            "moving_p": moving, "activity": "ходит" if moving > 0.5 else "нет движения", "activity_p": 0.8,
            "links_live": links_live}


def tracker(state_path=None):
    out = []
    tr = PersonTracker(publish=out.append, state_path=state_path)
    assert tr.set_plan(PLAN)
    return tr, out


def feed(tr, t0, t1, pa, moving=0.9, step=0.25, **kw):
    t = t0
    while t < t1 - 1e-9:
        tr.on_message(pred(t, pa, moving, **kw))
        t += step
    return t


def test_grid_moves_only_through_the_door():
    g = Grid(PLAN)
    assert set(g.labels) == {"A", "B"}
    cross = [(i, j) for i, j in zip(g.src, g.dst) if g.room[i] != g.room[j]]
    assert cross, "door must connect the rooms"
    for i, j in cross:
        mid = (g.xy[i] + g.xy[j]) / 2
        assert abs(mid[1] - 2.5) < 0.6


def test_unchanged_plan_is_not_rebuilt():
    tr, _ = tracker()
    assert not tr.set_plan(dict(PLAN))


def test_consistent_answers_move_the_person_and_one_spike_does_not():
    tr, out = tracker()
    t = feed(tr, 0.0, 6.0, pa=0.95)
    assert out[-1]["zone"] == "A" and out[-1]["zone_p"] > 0.8
    tr.on_message(pred(t, pa=0.01))  # одна уверенная ошибка
    t = feed(tr, t + 0.25, t + 1.5, pa=0.95)
    assert out[-1]["zone"] == "A"
    t = feed(tr, t, t + 8.0, pa=0.05)  # человек действительно перешёл
    assert out[-1]["zone"] == "B"
    assert 3.0 < out[-1]["xy"][0] < 6.0


def test_room_the_models_never_saw_moving_is_reached_through_the_links():
    plan = dict(PLAN)
    plan["rooms"] = PLAN["rooms"] + [{"name": "C", "polygon": [[6, 0], [9, 0], [9, 3], [6, 3]]}]
    plan["objects"] = PLAN["objects"] + [{"type": "дверь", "xy": [6.0, 1.5], "size": [0.8, 0.1, 2.0]},
                                         {"type": "датчик", "xy": [8.7, 0.3], "mac": "aa:00:00:00:00:03"}]
    plan["boards"] = PLAN["boards"] + [{"device_id": "d3", "xy": [8.7, 2.7]}]
    out = []
    tr = PersonTracker(publish=out.append)
    tr.set_plan(plan)
    t = 0.0
    for k in range(60):  # шумовой пол линий
        for dev, mac in (("d1", "aa:00:00:00:00:01"), ("d2", "aa:00:00:00:00:02"), ("d3", "aa:00:00:00:00:03")):
            tr.on_message({"type": "motion", "device_id": dev, "src": mac, "t": t, "raw": 1.0 + 0.05 * (k % 3)})
        t += 0.25
    for k in range(120):  # движение только на линии внутри C; модели C не знают («там никто не двигается»)
        tr.on_message({"type": "motion", "device_id": "d3", "src": "aa:00:00:00:00:03", "t": t, "raw": 5.0})
        for dev, mac in (("d1", "aa:00:00:00:00:01"), ("d2", "aa:00:00:00:00:02")):
            tr.on_message({"type": "motion", "device_id": dev, "src": mac, "t": t, "raw": 1.0})
        m = pred(t, pa=0.9 if k % 2 else 0.1, moving=0.9, source="server")
        m["counts_probs"] = {"A": [0.5, 0.5, 0, 0], "B": [0.5, 0.5, 0, 0], "C": [1.0, 0.0, 0, 0]}
        tr.on_message(m)
        t += 0.25
    assert out[-1]["zone"] == "C" and out[-1]["geo"]["zone"] == "C"


def test_stale_and_low_link_answers_are_ignored():
    tr, out = tracker()
    feed(tr, 0.0, 3.0, pa=0.95)
    b = tr.belief.copy()
    n = len(out)
    tr.on_message(pred(10.0, pa=0.0, links_live=2))  # мало живых линий
    assert np.allclose(tr.belief, b) and len(out) == n
    tr.on_message(pred(10.0, pa=0.95, source="server"))
    tr.on_message(pred(5.0, pa=0.0, source="server"))  # старше 2 с относительно текущего времени
    assert out[-1]["zone"] == "A"


def test_moving_has_hysteresis():
    tr, out = tracker()
    t = feed(tr, 0.0, 2.0, pa=0.9, moving=0.9)
    assert out[-1]["moving"]
    t = feed(tr, t, t + 1.5, pa=0.9, moving=0.05)  # короткая пауза — ещё «движется»
    assert out[-1]["moving"]
    t = feed(tr, t, t + 6.0, pa=0.9, moving=0.05)
    assert not out[-1]["moving"] and out[-1]["still_s"] > 0
    assert out[-1]["present"]


def test_disturbed_link_pulls_position_toward_its_line():
    tr, out = tracker()
    t = 0.0
    for k in range(60):  # шумовой пол обеих линий
        for dev, mac in (("d1", "aa:00:00:00:00:01"), ("d2", "aa:00:00:00:00:02")):
            tr.on_message({"type": "motion", "device_id": dev, "src": mac, "t": t, "raw": 1.0 + 0.05 * (k % 3)})
        t += 0.25
    for k in range(40):  # человек ходит у нижней линии (y = 0.5)
        tr.on_message({"type": "motion", "device_id": "d1", "src": "aa:00:00:00:00:01", "t": t, "raw": 5.0})
        tr.on_message({"type": "motion", "device_id": "d2", "src": "aa:00:00:00:00:02", "t": t, "raw": 1.0})
        tr.on_message(pred(t, pa=0.95, moving=0.9))
        t += 0.25
    assert out[-1]["zone"] == "A"
    assert out[-1]["xy"][1] < 1.3
    assert out[-1]["links_active"] >= 1
    snap = tr.snapshot()
    assert snap["links"]["d1|aa:00:00:00:00:01"]["on_plan"]
    assert out[-1]["geo"]["zone"] == "A"  # и без нейросети линии указывают на A


def test_heat_is_sent_about_once_a_second():
    tr, out = tracker()
    feed(tr, 0.0, 3.0, pa=0.9)
    heat = [m for m in out if "heat" in m]
    assert 2 <= len(heat) <= 4
    assert all(len(m["heat"]) <= 80 for m in heat)


def test_no_plan_no_messages():
    out = []
    tr = PersonTracker(publish=out.append)
    tr.on_message(pred(0.0, 0.9))
    assert out == []
    assert not tr.set_plan({"rooms": []}) or tr.grid is None


@pytest.mark.asyncio
async def test_hub_listener_publishes_person():
    hub = LiveHub()
    tr = PersonTracker(publish=hub.publish)
    tr.set_plan(PLAN)
    hub.listeners.append(tr.on_message)
    sub = hub.subscribe()
    hub.publish(pred(0.0, 0.9))
    kinds = []
    while not sub.queue.empty():
        kinds.append(sub.queue.get_nowait()["type"])
    assert kinds == ["pred", "person"]


def test_entrance_door_is_found():
    g = Grid(PLAN)
    assert len(g.exits) == 1 and np.allclose(g.exits[0], [0.0, 1.5])


def test_still_person_is_remembered_for_hours():
    tr, out = tracker()
    t = feed(tr, 0.0, 8.0, pa=0.05)  # пришёл в B
    assert out[-1]["zone"] == "B"
    t = feed(tr, t, t + 7200, pa=0.95, moving=0.02, step=5.0)  # 2 ч неподвижно, модель «шумит» про A
    assert out[-1]["present"] and out[-1]["status"] == "неподвижен"
    assert out[-1]["zone"] == "B" and out[-1]["still_s"] > 7000


def test_leaving_through_the_entrance_door():
    tr, out = tracker()
    feed(tr, 0.0, 6.0, pa=0.95)
    tr.xy = np.array([0.4, 1.5])  # последнее движение — у входной двери
    t = feed(tr, 6.0, 12.0, pa=0.95)
    tr.xy = np.array([0.4, 1.5])
    t = feed(tr, t, t + 60, pa=0.95, moving=0.02, step=1.0)
    assert out[-1]["present"]  # минуту у двери — ещё дома
    t = feed(tr, t, t + 200, pa=0.95, moving=0.02, step=1.0)
    assert not out[-1]["present"] and out[-1]["status"] == "вышел"
    feed(tr, t, t + 3, pa=0.95, moving=0.9)  # снова движение — снова дома
    assert out[-1]["present"]


def test_state_survives_restart(tmp_path):
    p = tmp_path / "person.json"
    tr, out = tracker(p)
    t = feed(tr, 0.0, 8.0, pa=0.05)
    feed(tr, t, t + 40, pa=0.05, moving=0.02, step=1.0)
    assert p.exists()
    tr2, out2 = tracker(p)
    feed(tr2, 100.0, 101.0, pa=0.95, moving=0.02)
    assert out2[-1]["present"] and out2[-1]["zone"] == "B" and out2[-1]["still_s"] > 0


def test_counts_and_pose_from_the_multi_head_model_are_smoothed():
    tr, out = tracker()
    t = 0.0
    for k in range(40):
        m = pred(t, pa=0.9)
        m["people_probs"] = [0.05, 0.15, 0.7, 0.1] if k != 20 else [0.0, 0.0, 0.0, 1.0]  # один выброс
        m["counts_probs"] = {"A": [0.1, 0.2, 0.7, 0.0], "B": [0.9, 0.1, 0.0, 0.0]}
        m["pose_probs"] = {"стоит": 0.8, "сидит": 0.2}
        tr.on_message(m)
        t += 0.25
    last = out[-1]
    assert last["people"]["n"] == 2 and last["counts"] == {"A": 2}
    assert "pose" not in last  # людей двое — позу одного не показываем
