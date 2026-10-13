"""Слабые метки из Home Assistant — учитель, независимый от CSI.

Трекер (физика по радиолиниям) сам выводится из CSI, поэтому модель, которую он учит, может унаследовать его ошибки.
Метка «дома никого» из HA (все person.* не дома: телефоны ушли из зоны home) от радио не зависит. Это сильная
отрицательная метка: в эти минуты любое «есть движение» — ложное срабатывание (кроме питомцев, робота-пылесоса и
гостей без телефона — поэтому это проверка, а не истина).

Журнал пишет tools/ha_labels.py: data/labels/ha.jsonl, строка = {"t": unix, "home": [...], "away": [...]}.
"""

from __future__ import annotations

import bisect
import json
from pathlib import Path


class HaLabels:
    """Состояния присутствия по времени; nobody_home(t) — только если в журнале нет пропусков вокруг t."""

    def __init__(self, rows: list[dict], max_gap_s: float = 120.0):
        rows = sorted((r for r in rows if "t" in r), key=lambda r: r["t"])
        self.t = [float(r["t"]) for r in rows]
        self.n_home = [len(r.get("home") or []) for r in rows]
        self.n_known = [len(r.get("home") or []) + len(r.get("away") or []) for r in rows]
        self.max_gap_s = max_gap_s

    @classmethod
    def load(cls, path: Path, since: float | None = None, max_gap_s: float = 120.0) -> "HaLabels":
        rows = []
        p = Path(path)
        if p.exists():
            with p.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue  # обрезанная последняя строка
                    if since is None or r.get("t", 0) >= since:
                        rows.append(r)
        return cls(rows, max_gap_s)

    def nobody_home(self, t: float, settle_s: float = 600.0) -> bool:
        """Все известные люди не дома весь интервал [t - settle_s, t] и журнал без дыр длиннее max_gap_s."""
        if not self.t:
            return False
        hi = bisect.bisect_right(self.t, t)  # последняя запись не позже t
        if hi == 0 or t - self.t[hi - 1] > self.max_gap_s:
            return False
        lo = bisect.bisect_right(self.t, t - settle_s)
        if lo == 0:
            return False  # нет записи, начатой до окна: неизвестно, что было в его начале
        lo -= 1
        prev = None
        for i in range(lo, hi):
            if self.n_known[i] == 0 or self.n_home[i] > 0:
                return False
            if prev is not None and self.t[i] - prev > self.max_gap_s:
                return False
            prev = self.t[i]
        return True

    def mask(self, times, settle_s: float = 600.0) -> list[bool]:
        return [self.nobody_home(float(t), settle_s) for t in times]
