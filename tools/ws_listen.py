"""Listen to the live WebSocket and print messages of the given types.

usage (wsn-server image, --network host): python ws_listen.py [--url ws://127.0.0.1:8080/ws/live] [--types breath] [--seconds 150]
"""
import argparse
import asyncio
import json
import time

import websockets


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8080/ws/live")
    ap.add_argument("--types", default="breath")
    ap.add_argument("--seconds", type=float, default=150)
    a = ap.parse_args()
    types = set(a.types.split(","))
    end = time.monotonic() + a.seconds
    async with websockets.connect(a.url, max_size=None) as ws:
        while (left := end - time.monotonic()) > 0:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=left))
            except asyncio.TimeoutError:
                break
            if m.get("type") in types:
                print(time.strftime("%H:%M:%S"), json.dumps(m, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
