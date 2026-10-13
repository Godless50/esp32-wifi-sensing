"""Записать сообщения /ws/live за N секунд в JSONL (для разбора качества на живых данных).

usage: python tools/ws_dump.py <seconds> <out.jsonl> [types,comma]
"""
import asyncio
import json
import sys
import time

import websockets


async def main(sec: float, out: str, types: set[str] | None) -> None:
    n = 0
    end = time.time() + sec
    async with websockets.connect("ws://127.0.0.1:8080/ws/live", max_size=None) as ws:
        with open(out, "w", encoding="utf-8") as f:
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                except asyncio.TimeoutError:
                    break
                try:
                    m = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if types and m.get("type") not in types:
                    continue
                m["_wall"] = time.time()
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} messages -> {out}")


if __name__ == "__main__":
    t = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    asyncio.run(main(float(sys.argv[1]), sys.argv[2], t))
