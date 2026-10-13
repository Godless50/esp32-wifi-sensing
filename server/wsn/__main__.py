"""Entry point: python -m wsn (env: WSN_UDP_PORT, WSN_HTTP_PORT, WSN_DATA, WSN_UI)."""

import logging
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .ingest import IngestService
from .keys import KeyStore
from .pinger import Pinger, PingerConfig
from .live import LiveHub
from .mirror import DatagramMirror
from .recorder import Recorder
from .store import Store


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    udp_port = int(os.environ.get("WSN_UDP_PORT", "5005"))
    http_port = int(os.environ.get("WSN_HTTP_PORT", "8080"))
    data = Path(os.environ.get("WSN_DATA", "/data"))
    ui = Path(os.environ.get("WSN_UI", "/ui"))
    keys = KeyStore(data / "device_keys.json")
    hub = LiveHub()
    store = Store(data / "wsn.db", data / "sessions")
    recorder = Recorder(store)
    (data / "run").mkdir(exist_ok=True)
    mirror = DatagramMirror(data / "run" / "csi.sock")  # копия потока для живого распознавания на хосте

    def on_raw(buf: bytes, device_id: str, recv_t: float) -> None:
        recorder.on_datagram(buf, device_id, recv_t)
        mirror.send(buf)

    ingest = IngestService(keys, hub.on_packet, state_path=data / "boot_state.json", on_raw=on_raw)
    def default_device() -> str | None:
        """The board that streamed most recently (within 10 s): the one a phone session records."""
        live = [d for d in hub.devices() if d.age_s < 10]
        return min(live, key=lambda d: d.age_s).device_id if live else None

    pinger = Pinger(PingerConfig.load(data / "links.json"))
    app = create_app(hub, ingest, ui, udp_port=udp_port, store=store, recorder=recorder,
                     default_device=default_device, pinger=pinger)
    uvicorn.run(app, host="0.0.0.0", port=http_port, log_level="info")


if __name__ == "__main__":
    main()
