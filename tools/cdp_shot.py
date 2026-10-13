"""Open a page in an already running headless Chrome (--remote-debugging-port), wait, print the text of some
elements and console errors, save a screenshot. For checking the UI pages from the server.

usage (wsn-server image, --network host): python cdp_shot.py URL OUT.png [--wait 8] [--ids personBox,where]
"""
import argparse
import asyncio
import base64
import json
import urllib.request

import websockets


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--wait", type=float, default=8.0)
    ap.add_argument("--ids", default="personBox")
    ap.add_argument("--size", default="1280x800", help="WxH; mobile e.g. 390x844")
    ap.add_argument("--dark", action="store_true")
    a = ap.parse_args()
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}/json/new?about:blank", method="PUT")
    tab = json.loads(urllib.request.urlopen(req).read())
    async with websockets.connect(tab["webSocketDebuggerUrl"], max_size=None) as ws:
        n = 0
        errors = []

        async def call(method, **params):
            nonlocal n
            n += 1
            my = n
            await ws.send(json.dumps({"id": my, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("method") in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown"):
                    p = m["params"]
                    if m["method"] == "Runtime.exceptionThrown" or p.get("type") == "error":
                        errors.append(json.dumps(p)[:300])
                if m.get("id") == my:
                    return m.get("result", {})

        await call("Runtime.enable")
        await call("Page.enable")
        w, h = (int(v) for v in a.size.split("x"))
        await call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=w < 600)
        await call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if a.dark else "light"}])
        await call("Page.navigate", url=a.url)
        end = asyncio.get_event_loop().time() + a.wait
        while asyncio.get_event_loop().time() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
                if m.get("method") in ("Runtime.consoleAPICalled", "Runtime.exceptionThrown"):
                    p = m["params"]
                    if m["method"] == "Runtime.exceptionThrown" or p.get("type") == "error":
                        errors.append(json.dumps(p)[:300])
            except asyncio.TimeoutError:
                pass
        for i in a.ids.split(","):
            r = await call("Runtime.evaluate", expression=f"document.getElementById({json.dumps(i)})?.textContent")
            print(f"#{i}:", r.get("result", {}).get("value"))
        shot = await call("Page.captureScreenshot", format="png")
        open(a.out, "wb").write(base64.b64decode(shot["data"]))
        print("errors:", errors or "none")
    urllib.request.urlopen(f"http://127.0.0.1:{a.port}/json/close/{tab['id']}").read()


if __name__ == "__main__":
    asyncio.run(main())
