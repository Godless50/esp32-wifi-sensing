"""Per-device HMAC keys stored in data/device_keys.json ({"aa:bb:..": "<64 hex>"})."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class KeyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._keys: dict[str, bytes] = {}
        self._mtime: int | None = None
        self.errors = 0
        self._reload()

    def _reload(self) -> None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            self._keys, self._mtime = {}, None
            return
        if self._mtime == st.st_mtime_ns:
            return
        self._mtime = st.st_mtime_ns  # битый файл не перечитывается на каждом пакете
        try:
            raw = json.loads(self.path.read_text() or "{}")
            self._keys = {k.lower(): bytes.fromhex(v) for k, v in raw.items()}
        except (ValueError, AttributeError, TypeError):
            self.errors += 1
            log.error("key file %s unreadable; keeping %d previously loaded keys", self.path, len(self._keys))

    def ids(self) -> list[str]:
        """Known boards in the order they were provisioned."""
        with self._lock:
            self._reload()
            return list(self._keys)

    def get(self, device_id: str) -> bytes | None:
        with self._lock:
            self._reload()
            return self._keys.get(device_id.lower())

    def set(self, device_id: str, key: bytes) -> None:
        with self._lock:
            self._reload()
            self._keys[device_id.lower()] = bytes(key)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".keys-")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump({k: v.hex() for k, v in self._keys.items()}, f, indent=2)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            self._mtime = self.path.stat().st_mtime_ns
