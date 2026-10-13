"""ml/ha_weak.py и разбор состояний в tools/ha_labels.py. Запуск: cd ml && python3 -m pytest -q test_ha_weak.py"""

import importlib.util
import json
from pathlib import Path

from ha_weak import HaLabels

_spec = importlib.util.spec_from_file_location("ha_labels", Path(__file__).resolve().parents[1] / "tools" / "ha_labels.py")
hl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hl)

AWAY = {"home": [], "away": ["person.a", "person.b"]}
HOME = {"home": ["person.a"], "away": ["person.b"]}


def rows(spec):
    return [{"t": t, **s} for t, s in spec]


def test_nobody_home_after_settle():
    lab = HaLabels(rows([(t, AWAY) for t in range(0, 1300, 30)]))
    assert not lab.nobody_home(300)        # меньше 10 минут от начала журнала: начало окна неизвестно
    assert lab.nobody_home(900)
    assert lab.nobody_home(1280)


def test_anyone_home_in_window_blocks_label():
    spec = [(t, AWAY) for t in range(0, 2000, 30)]
    spec[30] = (900, HOME)  # кто-то был дома в 900 с
    lab = HaLabels(rows(spec))
    assert not lab.nobody_home(1000)       # 900 внутри [400, 1000]
    assert lab.nobody_home(1600)           # окно [1000, 1600] чистое


def test_gap_in_log_blocks_label():
    spec = [(t, AWAY) for t in range(0, 600, 30)] + [(t, AWAY) for t in range(900, 2000, 30)]  # дыра 570..900
    lab = HaLabels(rows(spec))
    assert not lab.nobody_home(1000)       # дыра внутри окна
    assert lab.nobody_home(1600)
    assert not lab.nobody_home(5000)       # после конца журнала (последняя запись старше max_gap)


def test_unknown_people_only_is_not_a_label():
    lab = HaLabels(rows([(t, {"home": [], "away": []}) for t in range(0, 1300, 30)]))
    assert not lab.nobody_home(1000)
    assert not HaLabels([]).nobody_home(1000)


def test_load_skips_truncated_line(tmp_path):
    p = tmp_path / "ha.jsonl"
    p.write_text("\n".join(json.dumps({"t": t, **AWAY}) for t in range(0, 1300, 30)) + '\n{"t": 13', encoding="utf-8")
    lab = HaLabels.load(p)
    assert lab.nobody_home(1200)
    assert HaLabels.load(tmp_path / "missing.jsonl").t == []


def test_classify_states():
    states = [{"entity_id": "person.a", "state": "home"}, {"entity_id": "person.b", "state": "not_home"},
              {"entity_id": "person.c", "state": "Работа"}, {"entity_id": "person.d", "state": "unknown"},
              {"entity_id": "light.x", "state": "on"}]
    assert hl.classify(states, None) == {"home": ["person.a"], "away": ["person.b", "person.c"]}
    assert hl.classify(states, {"person.b"}) == {"home": [], "away": ["person.b"]}


def test_load_env(tmp_path):
    p = tmp_path / "ha.env"
    p.write_text("# comment\nHA_URL=http://h:8123\nHA_TOKEN='abc'\n\nHA_PERSONS=person.a, person.b\n", encoding="utf-8")
    env = hl.load_env(p)
    assert env["HA_TOKEN"] == "abc" and env["HA_URL"] == "http://h:8123"
