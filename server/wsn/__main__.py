"""Entry point: python -m wsn (env: WSN_UDP_PORT, WSN_HTTP_PORT, WSN_DATA, WSN_UI)."""

import logging
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .ingest import IngestService
from .keys import KeyStore
from .live import LiveHub


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    udp_port = int(os.environ.get("WSN_UDP_PORT", "5005"))
    http_port = int(os.environ.get("WSN_HTTP_PORT", "8080"))
    data = Path(os.environ.get("WSN_DATA", "/data"))
    ui = Path(os.environ.get("WSN_UI", "/ui"))
    keys = KeyStore(data / "device_keys.json")
    hub = LiveHub()
    ingest = IngestService(keys, hub.on_packet)
    app = create_app(hub, ingest, ui, udp_port=udp_port)
    uvicorn.run(app, host="0.0.0.0", port=http_port, log_level="info")


if __name__ == "__main__":
    main()
