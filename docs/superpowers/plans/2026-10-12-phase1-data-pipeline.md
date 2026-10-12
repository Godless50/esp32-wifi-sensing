# Фаза 1: конвейер данных CSI — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Плата ESP32-S3 снимает CSI с ping-ответов привязанного узла mesh (≥ 80 Гц), отправляет подписанные
UDP-пачки на сервер, сервер принимает их и показывает живую «водопадную» картину амплитуд и телеметрию на `/device`.

**Architecture:** Прошивка ESP-IDF 5.4 (задачи wifi_mgr, ping_gen, csi_capture, uplink, console) + чистый C-компонент
`wsn_proto` (кодирование пакетов, тестируется на хосте). Сервер — пакет Python `wsn` в Docker: `protocol` (разбор/HMAC),
`ingest` (UDP, защита от повторов), `live` (амплитуды, статистика, WebSocket-хаб), `app` (FastAPI). UI — статическая страница.

**Tech Stack:** ESP-IDF `espressif/idf:release-v5.4` (Docker), C11, OpenSSL 3 (хост-тесты), Python 3.12
(`python:3.12-slim`), FastAPI, uvicorn, numpy, pyserial, pytest, pytest-asyncio, vanilla JS + Canvas.

**Spec:** `docs/superpowers/specs/2026-10-12-wifi-sensing-design.md` (разделы 3, 4.1–4.3, 4.5, 4.6, 6.1; фазы 2 и 3 —
отдельные планы).

## Global Constraints

- Все команды выполняются на сервере `server` в `/home/ops/esp32-wifi-sensing`; плата на `/dev/ttyACM0`.
- Сборка прошивки только в Docker: `docker run --rm -v "$PWD/firmware":/project -w /project -u $(id -u):$(id -g) -e HOME=/tmp espressif/idf:release-v5.4 idf.py build`; для прошивки добавить `--group-add $(getent group dialout | cut -d: -f3) --device /dev/ttyACM0` и `-p /dev/ttyACM0 flash`.
- Сервер и его тесты — в образе `wsn-server` (собирается из `deploy/Dockerfile.server`); на хосте Python не используется.
- Протокол little-endian; заголовок: `"WSN1"`, `ver=1`, `type` (1=CSI, 3=TELEM; 2=PRED — фаза 3), `device_id` 6 байт MAC STA, `boot_id u32`, `seq u32`, `n u8`; в конце `hmac` = первые 16 байт HMAC-SHA256 по всем предыдущим байтам. Длина заголовка 21 байт.
- CSI-кадр: `t_us u64, rssi i8, noise i8, sig_mode u8, cwb u8, len u16, data i8[len]` (14 байт + len).
- TELEM: `csi_hz f32, drop_ratio f32, rssi_avg i8, heap_free u32, psram_free u32, uptime_s u32, fw_ver u32, model_ver u32, wifi_state u8` (30 байт), `n=1`.
- `wifi_state`: 0=IDLE, 1=CONNECTING, 2=CONNECTED, 3=BSSID_NOT_FOUND, 4=AUTH_FAIL.
- Датаграмма ≤ 1400 байт; CSI-пачка ≤ 10 кадров; сброс пачки не реже раза в 50 мс.
- Порты: UDP 5005, HTTP 8080 (только фаза 1; TLS 8443 — фаза 2).
- Ключи устройств: `data/device_keys.json` (`{"aa:bb:cc:dd:ee:ff": "<64 hex>"}`), `data/` в `.gitignore`. Пароль Wi-Fi нигде в репозитории не хранится.
- CSI-конфиг: `lltf_en=1, htltf_en=1, stbc_htltf2_en=0, ltf_merge_en=0, channel_filter_en=0`; принимаются кадры только с MAC = BSSID узла.
- `ping_hz` по умолчанию 100; переподключение Wi-Fi 1→30 с экспоненциально; на другой BSSID плата не переключается.
- Разметка флеша сразу финальная (раздел 4.4 спецификации), чтобы фаза 3 не требовала перепрошивки разделов.
- Коммиты завершаются строками:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` и `Claude-Session: https://claude.ai/code/session_01UJU3gKdYKjCwHXgSmdTmyA`.

## Review Focus

1. Перезагрузка платы → `seq` снова с 0 при новом `boot_id`: сервер обязан принять поток, а повтор пакетов старой загрузки — отвергнуть (тест в Task 3).
2. Пакет от неизвестного MAC или с неверным ключом: молча отбросить, посчитать в `rejected`, показать в `/api/devices` как `unknown` без падения (тест в Task 3).
3. Пароль Wi-Fi с пробелами, кавычками и `\`: `provision.py` должен экранировать так, чтобы `esp_console_split_argv` восстановил исходную строку (тест в Task 6).
4. CSI длиной 128 (L-LTF), 256 (L-LTF+HT-LTF) и 384 (со STBC): разбор и амплитуды не должны падать на любой длине, неизвестная длина — кадр пропускается (тест в Task 4).
5. Вкладка UI закрыта посреди отправки или «медленный» клиент: хаб удаляет клиента, приём UDP не блокируется (тест в Task 4).

---

## Файловая структура

```
shared/protocol.md                 описание протокола (копия Global Constraints + таблица полей)
server/pyproject.toml              пакет wsn, зависимости, настройки pytest
server/wsn/__init__.py
server/wsn/protocol.py             dataclass'ы, parse_packet, encode_packet, ProtocolError
server/wsn/keys.py                 KeyStore: загрузка/запись data/device_keys.json
server/wsn/ingest.py               IngestService (asyncio.DatagramProtocol), защита от повторов, счётчики
server/wsn/live.py                 ltf_amplitude, DeviceStats, LiveHub
server/wsn/app.py                  FastAPI: /api/devices, /ws/live, статика /device
server/wsn/__main__.py             запуск uvicorn + UDP
server/tests/...                   pytest
firmware/CMakeLists.txt, partitions.csv, sdkconfig.defaults
firmware/components/wsn_proto/     wsn_proto.c/.h (чистый C), test/host_test.c, test/golden.c, Makefile
firmware/main/                     app_main.c, settings.c/.h, console_cmds.c, wifi_mgr.c/.h,
                                   ping_gen.c/.h, csi_capture.c/.h, uplink.c/.h, CMakeLists.txt
ui/device/index.html, device.js    живая страница
deploy/Dockerfile.server, docker-compose.yml
tools/provision.py                 провижининг платы по USB-консоли
tools/hil_csi_rate.sh              HIL-проверка частоты CSI
```

---

### Task 1: Протокол на стороне сервера

**Files:**
- Create: `server/pyproject.toml`, `server/wsn/__init__.py`, `server/wsn/protocol.py`, `deploy/Dockerfile.server`, `shared/protocol.md`
- Test: `server/tests/test_protocol.py`

**Interfaces:**
- Produces:
  - `class ProtocolError(ValueError)`
  - `@dataclass(frozen=True) class CsiFrame: t_us:int; rssi:int; noise:int; sig_mode:int; cwb:int; data:bytes`
  - `@dataclass(frozen=True) class Telemetry: csi_hz:float; drop_ratio:float; rssi_avg:int; heap_free:int; psram_free:int; uptime_s:int; fw_ver:int; model_ver:int; wifi_state:int`
  - `@dataclass(frozen=True) class Packet: type:int; device_id:str; boot_id:int; seq:int; frames:list[CsiFrame]; telemetry:Telemetry|None` (`device_id` — MAC в виде `"aa:bb:cc:dd:ee:ff"`)
  - `TYPE_CSI=1, TYPE_TELEM=3, HEADER_LEN=21, HMAC_LEN=16, MAX_DATAGRAM=1400`
  - `peek_device_id(buf: bytes) -> str` (без проверки подписи; для поиска ключа)
  - `parse_packet(buf: bytes, key: bytes) -> Packet` — проверяет magic, ver, длины, HMAC (`hmac.compare_digest`), тип
  - `encode_packet(type:int, device_id:str, boot_id:int, seq:int, key:bytes, frames:list[CsiFrame]|None=None, telemetry:Telemetry|None=None) -> bytes`

- [ ] **Step 1: Dockerfile и pyproject**

`deploy/Dockerfile.server`: `FROM python:3.12-slim`, `WORKDIR /app`, копирует `server/`, `pip install --no-cache-dir -e ".[test]"`.
Зависимости: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `numpy>=2.0`, `pyserial>=3.5`; extra `test`: `pytest>=8`, `pytest-asyncio>=0.24`, `httpx>=0.27`. В `pyproject.toml`: `[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`.
Сборка: `docker build -f deploy/Dockerfile.server -t wsn-server .`
Тесты везде запускаются так: `docker run --rm -v "$PWD/server":/app wsn-server pytest -q <путь>`.

- [ ] **Step 2: Написать падающие тесты**

```python
KEY = bytes(range(32))
MAC = "02:00:00:00:00:01"  # STA MAC платы (тестовый)

def test_csi_roundtrip():
    frames = [CsiFrame(t_us=1_000_000 + i, rssi=-45, noise=-92, sig_mode=1, cwb=0, data=bytes(range(256)))
              for i in range(5)]
    buf = encode_packet(TYPE_CSI, MAC, boot_id=0xDEADBEEF, seq=7, key=KEY, frames=frames)
    assert len(buf) == 21 + 5 * (14 + 256) + 16
    p = parse_packet(buf, KEY)
    assert (p.type, p.device_id, p.boot_id, p.seq) == (TYPE_CSI, MAC, 0xDEADBEEF, 7)
    assert p.frames == frames and p.telemetry is None

def test_telemetry_roundtrip():
    t = Telemetry(98.5, 0.02, -44, 120_000, 8_000_000, 3600, 1, 0, 2)
    p = parse_packet(encode_packet(TYPE_TELEM, MAC, 1, 0, KEY, telemetry=t), KEY)
    assert p.telemetry.csi_hz == pytest.approx(98.5) and p.telemetry.drop_ratio == pytest.approx(0.02)
    assert (p.telemetry.rssi_avg, p.telemetry.heap_free, p.telemetry.uptime_s, p.telemetry.wifi_state) == (-44, 120_000, 3600, 2)

def test_bad_hmac_rejected():
    buf = bytearray(encode_packet(TYPE_TELEM, MAC, 1, 0, KEY, telemetry=Telemetry(1, 0, -1, 1, 1, 1, 1, 0, 2)))
    buf[-1] ^= 1
    with pytest.raises(ProtocolError, match="hmac"):
        parse_packet(bytes(buf), KEY)

@pytest.mark.parametrize("mutate", ["magic", "ver", "truncated", "type"])
def test_malformed_rejected(mutate): ...  # каждая порча → ProtocolError (magic=b"XXXX", ver=2, обрезка на 10 байт, type=9)

def test_frame_len_overflow_rejected():  # len в кадре больше остатка буфера → ProtocolError("length")

def test_peek_device_id():
    assert peek_device_id(encode_packet(TYPE_CSI, MAC, 1, 0, KEY, frames=[])) == MAC
```

- [ ] **Step 3: Запустить — ожидается FAIL** (`ModuleNotFoundError: wsn.protocol`)

- [ ] **Step 4: Реализовать `server/wsn/protocol.py`** через `struct` (`"<4sBB6sIIB"` для заголовка, `"<QbbBBH"` для кадра, `"<ffbIIIIIB"` для TELEM); HMAC — `hmac.new(key, msg, "sha256").digest()[:16]`. `encode_packet` бросает `ProtocolError("too large")`, если итог > `MAX_DATAGRAM`. `shared/protocol.md` — таблица полей из Global Constraints.

- [ ] **Step 5: Запустить — ожидается PASS** (`docker run ... pytest -q tests/test_protocol.py` → все зелёные)

- [ ] **Step 6: Commit** — `feat(server): packet protocol with HMAC`

---

### Task 2: C-компонент `wsn_proto` и перекрёстная проверка с Python

**Files:**
- Create: `firmware/components/wsn_proto/include/wsn_proto.h`, `firmware/components/wsn_proto/wsn_proto.c`, `firmware/components/wsn_proto/CMakeLists.txt`, `firmware/components/wsn_proto/test/host_test.c`, `firmware/components/wsn_proto/test/golden.c`, `firmware/components/wsn_proto/test/Makefile`
- Test: `server/tests/test_c_golden.py`

**Interfaces:**
- Consumes: формат из Task 1 (`parse_packet`).
- Produces (C):
  ```c
  typedef void (*wsn_hmac_fn)(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t len, uint8_t out[32]);
  typedef struct { uint8_t device_id[6]; uint32_t boot_id; uint32_t seq; const uint8_t *key; size_t key_len; wsn_hmac_fn hmac; } wsn_ctx_t;
  typedef struct { uint64_t t_us; int8_t rssi, noise; uint8_t sig_mode, cwb; uint16_t len; const int8_t *data; } wsn_csi_frame_t;
  typedef struct { float csi_hz, drop_ratio; int8_t rssi_avg; uint32_t heap_free, psram_free, uptime_s, fw_ver, model_ver; uint8_t wifi_state; } wsn_telem_t;
  #define WSN_MAX_DATAGRAM 1400
  #define WSN_MAX_FRAMES 10
  size_t wsn_frames_fit(const wsn_csi_frame_t *frames, size_t n);         /* сколько первых кадров влезает в 1400 и ≤ 10 */
  int wsn_encode_csi(wsn_ctx_t *ctx, const wsn_csi_frame_t *frames, size_t n, uint8_t *out, size_t cap); /* >0 длина, <0 ошибка; seq++ */
  int wsn_encode_telem(wsn_ctx_t *ctx, const wsn_telem_t *t, uint8_t *out, size_t cap);
  ```

- [ ] **Step 1: Падающие хост-тесты `host_test.c`** (без фреймворка, `assert`):
  - `wsn_frames_fit` для 10 кадров по 256 байт → `5`; по 384 → `3`; по 128 → `9`; 0 кадров → `0`.
  - `wsn_encode_csi` с `cap` меньше нужного → `< 0`, `seq` не меняется; успешный вызов увеличивает `seq` на 1.
  - Длина пакета для 5×256 = `21 + 5*270 + 16 = 1387`.
- [ ] **Step 2: `golden.c`** — пишет в stdout (hex) пакет CSI с ключом `0x00..0x1f`, MAC `02:00:00:00:00:01`, `boot_id=0xDEADBEEF`, `seq=7`, 5 кадров `t_us=1000000+i, rssi=-45, noise=-92, sig_mode=1, cwb=0, len=256, data[j]=(int8_t)j`, затем строку с пакетом TELEM из Task 1. HMAC на хосте — OpenSSL `HMAC(EVP_sha256(), ...)`.
- [ ] **Step 3: `test_c_golden.py`** читает `tests/golden/c_packets.hex` (генерируется `make -C firmware/components/wsn_proto/test golden`) и проверяет, что `parse_packet` возвращает ровно поля из Step 2 и что байты совпадают с `encode_packet` для тех же входов.
- [ ] **Step 4: Запустить — FAIL** (`make -C firmware/components/wsn_proto/test test` не собирается).
- [ ] **Step 5: Реализовать `wsn_proto.c`** — запись little-endian побайтово (без `memcpy` структур, чтобы не зависеть от выравнивания), HMAC через `ctx->hmac`, в пакет 16 байт. `CMakeLists.txt` компонента: `idf_component_register(SRCS "wsn_proto.c" INCLUDE_DIRS "include")`.
- [ ] **Step 6: PASS** — `make -C firmware/components/wsn_proto/test test` выводит `OK`; `make ... golden` + `docker run ... pytest -q tests/test_c_golden.py` зелёный.
- [ ] **Step 7: Commit** — `feat(fw): host-testable wsn_proto encoder, golden cross-check`

---

### Task 3: Хранилище ключей и приём UDP

**Files:**
- Create: `server/wsn/keys.py`, `server/wsn/ingest.py`
- Test: `server/tests/test_keys.py`, `server/tests/test_ingest.py`

**Interfaces:**
- Consumes: `parse_packet`, `peek_device_id`, `Packet`, `ProtocolError` (Task 1).
- Produces:
  - `class KeyStore: __init__(path: Path); get(device_id: str) -> bytes|None; set(device_id: str, key: bytes) -> None` (атомарная запись через временный файл + `os.replace`, права `0o600`)
  - `class IngestService(asyncio.DatagramProtocol): __init__(keys: KeyStore, on_packet: Callable[[Packet, float], None])` — `float` = `time.monotonic()` приёма
  - `IngestService.handle(buf: bytes, recv_t: float) -> bool` (синхронная логика, вызывается из `datagram_received`)
  - `IngestService.counters: dict[str, int]` с ключами `accepted, rejected_unknown, rejected_hmac, rejected_malformed, rejected_replay`
  - `IngestService.unknown_devices: set[str]`

- [ ] **Step 1: Падающие тесты**
  - `test_keystore_roundtrip_and_perms` — `set` затем новый `KeyStore(path).get` возвращает ключ; `stat.S_IMODE(path.stat().st_mode) == 0o600`.
  - `test_accepts_valid` — `handle` → `True`, `on_packet` вызван 1 раз, `counters["accepted"] == 1`.
  - `test_unknown_device` — MAC без ключа → `False`, `counters["rejected_unknown"] == 1`, MAC в `unknown_devices`.
  - `test_hmac_and_malformed` — неверный ключ → `rejected_hmac`; 5 случайных байт → `rejected_malformed`.
  - `test_replay_same_boot` — seq 5, затем снова 5, затем 4 → второй и третий `False`, `rejected_replay == 2`.
  - `test_reboot_new_boot_id` — boot A seq 100, затем boot B seq 0 → принят; затем boot A seq 101 → `False` (старая загрузка).
  - `test_gap_allowed` — boot A seq 1, затем seq 50 → принят (потери UDP допустимы).
- [ ] **Step 2: FAIL**
- [ ] **Step 3: Реализовать.** Состояние на устройство: `current_boot: int`, `last_seq: int`, `retired: deque[int]` (maxlen 64). Новый `boot_id`, не входящий в `retired`, становится текущим, старый уходит в `retired`.
- [ ] **Step 4: PASS** (`pytest -q tests/test_keys.py tests/test_ingest.py`)
- [ ] **Step 5: Commit** — `feat(server): UDP ingest with HMAC and replay protection`

---

### Task 4: Живая статистика, хаб WebSocket и FastAPI

**Files:**
- Create: `server/wsn/live.py`, `server/wsn/app.py`, `server/wsn/__main__.py`, `deploy/docker-compose.yml`
- Test: `server/tests/test_live.py`, `server/tests/test_app.py`

**Interfaces:**
- Consumes: `Packet`, `CsiFrame`, `Telemetry` (Task 1); `IngestService`, `KeyStore` (Task 3).
- Produces:
  - `ltf_amplitude(data: bytes, part: Literal["lltf", "ht"]) -> np.ndarray | None` — 64 значения `float32`, переупорядоченные в порядке поднесущих −32…31; `None`, если длина не 128/256/384 или для `"ht"` при длине 128. Раскладка буфера ESP32 (документация Espressif «Wi-Fi Channel State Information», вторичный канал none): L-LTF байты 0..127, HT-LTF байты 128..255; внутри каждого LTF индексы поднесущих 0..31, затем −32..−1; каждая поднесущая — 2 байта `imag, real` (int8).
  - `@dataclass class DeviceStats: device_id:str; csi_hz:float; last_seen:float; rssi:int|None; telemetry:Telemetry|None; counters:dict[str,int]`
  - `class LiveHub: __init__(decimate_hz: float = 20.0); on_packet(p: Packet, recv_t: float) -> None; devices() -> list[DeviceStats]; async subscribe() -> AsyncIterator[dict]`
    Сообщения хаба (JSON): `{"type":"amp","device_id":..., "t":recv_t, "rssi":..., "amp":[64 float]}` не чаще `decimate_hz` на устройство; `{"type":"telem","device_id":..., ...поля Telemetry}`. Очередь клиента `asyncio.Queue(maxsize=200)`; при переполнении клиент отключается.
  - `create_app(hub: LiveHub, ingest: IngestService, ui_dir: Path) -> FastAPI`: `GET /api/devices` → список `DeviceStats` + `unknown_devices` + `counters`; `WS /ws/live`; `GET /device` → `ui/device/index.html`; статика `/static` → `ui_dir`.
  - `python -m wsn` — читает env `WSN_UDP_PORT=5005`, `WSN_HTTP_PORT=8080`, `WSN_DATA=/data`, `WSN_UI=/ui`; поднимает UDP-эндпоинт в lifespan FastAPI.
  - `csi_hz` на сервере — число кадров за последние 2 с / 2.

- [ ] **Step 1: Падающие тесты `test_live.py`**
  - `test_amplitude_lengths` — длины 128 (`lltf`), 256, 384 (`lltf` и `ht`) → массив формы `(64,)`; 128 с `"ht"` → `None`; длина 200 → `None`.
  - `test_amplitude_order` — буфер, где у поднесущей с индексом `+1` (`imag=3, real=4`), остальные нули → в результате `amp[33] == 5.0` (−32 → позиция 0, значит +1 → позиция 33), остальные 0.
  - `test_csi_hz` — 200 кадров с `recv_t` равномерно за 2 с → `csi_hz == pytest.approx(100, rel=0.05)`.
  - `test_decimation` — 100 кадров за 1 с → подписчик получает 20±1 сообщений `amp`.
  - `test_slow_client_dropped` — подписчик, который не читает; после 300 сообщений он отключён, `on_packet` ни разу не блокировался (вызов выполняется < 5 мс).
- [ ] **Step 2: Падающие тесты `test_app.py`** (`httpx.AsyncClient` + `ASGITransport`, WS — через `starlette.testclient.TestClient`):
  - `/api/devices` после одного принятого пакета содержит устройство с `device_id == MAC`.
  - `/ws/live` получает сообщение `amp` после `hub.on_packet(...)`.
  - `/device` → 200 и `text/html`.
- [ ] **Step 3: FAIL**
- [ ] **Step 4: Реализовать** `live.py`, `app.py`, `__main__.py`. `docker-compose.yml`: сервис `api` (образ `wsn-server`, команда `python -m wsn`, тома `../data:/data`, `../ui:/ui:ro`, порты `5005:5005/udp`, `8080:8080`, `restart: unless-stopped`).
- [ ] **Step 5: PASS** (`pytest -q` — весь набор)
- [ ] **Step 6: Commit** — `feat(server): live hub, REST and WebSocket API`

---

### Task 5: Страница `/device`

**Files:**
- Create: `ui/device/index.html`, `ui/device/device.js`, `tools/replay.py`
- Test: `server/tests/test_replay.py`

**Interfaces:**
- Consumes: `/api/devices`, `/ws/live` (Task 4); `encode_packet` (Task 1).
- Produces: `tools/replay.py --host H --port 5005 --key-hex K --mac M --hz 100 --seconds N [--from-file csi.bin]` — без `--from-file` шлёт синтетические CSI (синус по поднесущим, меняющийся во времени) + TELEM раз в 1 с. Функция `synth_frames(n:int, hz:float, t0_us:int) -> list[CsiFrame]` для тестов.

- [ ] **Step 1: Падающий тест** `test_replay.py`: `synth_frames(100, 100.0, 0)` → 100 кадров, `t_us` с шагом 10 000, `len == 256`; прогон 1 с через `IngestService.handle` с тем же ключом → `accepted ≥ 20` пакетов (кадры упакованы по 5).
- [ ] **Step 2: FAIL → реализовать `replay.py` → PASS**
- [ ] **Step 3: UI.** Без сборщиков и CDN (локальная сеть). Элементы: выбор устройства; карточки `CSI Гц (сервер / плата)`, `RSSI`, `drop_ratio`, `wifi_state` текстом (`CONNECTED` и т. п.), `uptime`, `heap/psram`, версии; Canvas-«водопад» 64 поднесущих × последние 10 с (20 строк/с), цвет — линейная шкала по p5…p95 амплитуд текущего экрана; счётчики `rejected_*`; список `unknown_devices`. При потере WS — переподключение через 2 с и надпись «нет связи с сервером». Предупреждение, если `drop_ratio > 0.2` (раздел 4.2 спецификации) или `csi_hz < 80`.
- [ ] **Step 4: Проверка вживую.** `docker compose -f deploy/docker-compose.yml up -d`, ключ тестового MAC в `data/device_keys.json`, `docker run --rm --network host -v "$PWD":/w wsn-server python /w/tools/replay.py --host 127.0.0.1 --port 5005 --key-hex ... --mac 02:00:00:00:00:01 --hz 100 --seconds 30`. Открыть `http://server:8080/device` во встроенном браузере. Ожидается: водопад движется, `CSI Гц (сервер)` ≈ 100, предупреждений нет.
- [ ] **Step 5: Commit** — `feat(ui): live device page and replay tool`

---

### Task 6: Прошивка — настройки, консоль, Wi-Fi, провижининг

**Files:**
- Create: `firmware/CMakeLists.txt`, `firmware/partitions.csv`, `firmware/sdkconfig.defaults`, `firmware/main/CMakeLists.txt`, `firmware/main/app_main.c`, `firmware/main/settings.c`, `firmware/main/settings.h`, `firmware/main/console_cmds.c`, `firmware/main/wifi_mgr.c`, `firmware/main/wifi_mgr.h`, `tools/provision.py`
- Test: `server/tests/test_provision.py`

**Interfaces:**
- Produces (C):
  ```c
  typedef struct { char ssid[33]; char pass[65]; uint8_t bssid[6]; char host[64]; uint16_t port; uint8_t key[32]; uint16_t ping_hz; uint8_t csi_source; /*0=ht,1=lltf*/ } wsn_settings_t;
  esp_err_t settings_load(wsn_settings_t *out);   /* отсутствующие поля → значения по умолчанию: port=5005, ping_hz=100, csi_source=0 */
  esp_err_t settings_save(const wsn_settings_t *s);
  typedef enum { WIFI_ST_IDLE=0, WIFI_ST_CONNECTING=1, WIFI_ST_CONNECTED=2, WIFI_ST_BSSID_NOT_FOUND=3, WIFI_ST_AUTH_FAIL=4 } wifi_state_t;
  esp_err_t wifi_mgr_start(const wsn_settings_t *s);
  wifi_state_t wifi_mgr_state(void);
  bool wifi_mgr_gateway(esp_ip4_addr_t *gw);       /* true, если есть IP */
  ```
  Консоль: `wifi set <ssid> <pass> <bssid>`, `server set <host> <port>`, `key set <64hex>`, `ping_hz set <1..200>`, `csi_source set ht|lltf`, `status` (печатает строку `STATUS wifi=<state> rssi=<dBm> ip=<ip> gw=<gw> bssid=<mac> csi_hz=<f> fw=<n>`; пароль и ключ не печатает), `reboot`.
- Produces (Python): `tools/provision.py --port /dev/ttyACM0 --ssid S --bssid M --host H [--udp-port 5005] [--keys data/device_keys.json]`; пароль читается через `getpass` или env `WSN_WIFI_PASS`; ключ генерируется `secrets.token_bytes(32)`, если для MAC платы его нет; MAC платы берётся из вывода `status` (`mac=` — добавить в строку `STATUS`). Функция `console_quote(s: str) -> str`.

- [ ] **Step 1: Падающий тест** `test_provision.py::test_console_quote` для строк `'abc'`, `'a b'`, `'a"b'`, `'a\\b'`, `'пароль 1'` — результат, разобранный эталонным сплиттером (реализовать в тесте по правилам `esp_console_split_argv`: аргумент в двойных кавычках, внутри `\"` и `\\`), равен исходной строке.
- [ ] **Step 2: FAIL → реализовать `console_quote` → PASS**
- [ ] **Step 3: Прошивка.** `partitions.csv` по разделу 4.4 спецификации (nvs 0x6000, otadata 0x2000, phy_init 0x1000, ota_0 3M, ota_1 3M, model_a 1M, model_b 1M; тип `data`, подтипы `0x40`/`0x41` для model). `sdkconfig.defaults`: `CONFIG_ESP_WIFI_CSI_ENABLED=y`, `CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y`, `CONFIG_PARTITION_TABLE_CUSTOM=y`, `CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions.csv"`, `CONFIG_SPIRAM=y`, `CONFIG_SPIRAM_MODE_OCT=y`, `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`, `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`, `CONFIG_ESP_TASK_WDT_EN=y`. `wifi_mgr`: `sta_config.bssid_set=1`, `channel=0`, `esp_wifi_set_ps(WIFI_PS_NONE)`, повтор 1,2,4,…,30 с; причина отказа `WIFI_REASON_NO_AP_FOUND` → `BSSID_NOT_FOUND`, `AUTH_FAIL`/`4WAY_HANDSHAKE_TIMEOUT` → `AUTH_FAIL`. Без сохранённых настроек — только консоль, `wifi=IDLE`.
- [ ] **Step 4: Сборка** — команда из Global Constraints; ожидается `Project build complete`.
- [ ] **Step 5: Прошивка и провижининг.** Прошить. Спросить у пользователя пароль Wi-Fi (не сохранять в файлах и логах), выполнить `provision.py --ssid HomeNet --bssid <выбранный пользователем узел; по умолчанию 98:b3:ef:02:da:28> --host <IP сервера в LAN>`. Ожидается: `status` → `wifi=CONNECTED`, есть `ip=` и `gw=`; в `data/device_keys.json` появился MAC платы.
- [ ] **Step 6: Commit** — `feat(fw): settings, console, locked-BSSID wifi manager, provisioning`

---

### Task 7: Прошивка — ping, CSI, uplink, телеметрия; HIL

**Files:**
- Create: `firmware/main/ping_gen.c/.h`, `firmware/main/csi_capture.c/.h`, `firmware/main/uplink.c/.h`, `tools/hil_csi_rate.sh`
- Modify: `firmware/main/app_main.c`, `firmware/main/CMakeLists.txt` (REQUIRES `wsn_proto mbedtls esp_wifi lwip`)

**Interfaces:**
- Consumes: `wsn_settings_t`, `wifi_mgr_*` (Task 6); `wsn_ctx_t`, `wsn_encode_csi`, `wsn_encode_telem`, `wsn_frames_fit` (Task 2).
- Produces (C):
  ```c
  esp_err_t ping_gen_start(esp_ip4_addr_t gw, uint16_t hz);  void ping_gen_stop(void);
  esp_err_t csi_capture_start(const uint8_t bssid[6], uint8_t csi_source);
  typedef struct { uint32_t accepted, dropped; } csi_counters_t;  void csi_capture_counters(csi_counters_t *out);
  bool csi_capture_pop(wsn_csi_frame_t *out, uint8_t *data_buf, TickType_t wait); /* data_buf ≥ 384 */
  esp_err_t uplink_start(const wsn_settings_t *s);
  ```
- Поведение: колбэк CSI копирует `len` + данные в `xRingbufferCreateWithCaps(..., MALLOC_CAP_SPIRAM)` на 4 с (~100 Гц × 400 байт ≈ 160 КБ); кадр с MAC ≠ BSSID не пишется вовсе; кадр, не подходящий под `csi_source` (`ht` → `sig_mode==1` и `len ≥ 256`), считается `dropped`, но в фазе 1 всё равно отправляется сырым (для анализа). `uplink`: пачка до `wsn_frames_fit`, сброс по 50 мс; `boot_id = esp_random()` при старте; HMAC — `mbedtls_md_hmac` (SHA256); TELEM раз в 1 с: `csi_hz` по `accepted` за секунду, `drop_ratio = dropped/(accepted+dropped)`, `rssi_avg` по кадрам секунды, `fw_ver=1`, `model_ver=0`. Сторож: `csi_hz < 20` 10 с подряд при `wifi=CONNECTED` → `ping_gen_stop(); ping_gen_start(...)`, лог `WARN csi stalled`. `ping_gen` стартует на событии получения IP и останавливается при отключении.

- [ ] **Step 1: `tools/hil_csi_rate.sh [seconds=30] [min_hz=80]`** — опрашивает `http://127.0.0.1:8080/api/devices` раз в секунду, считает среднее `csi_hz` (сервер) по MAC платы из `data/device_keys.json`, печатает `HIL csi_hz=<среднее> min=<мин> PASS|FAIL`, код выхода 1 при FAIL. Запуск сейчас (плата ещё без CSI) → `FAIL` — это ожидаемый «красный» тест.
- [ ] **Step 2: Реализовать `ping_gen`, `csi_capture`, `uplink`, связать в `app_main`.**
- [ ] **Step 3: Сборка, прошивка** (настройки в NVS сохраняются — раздел nvs не стирается).
- [ ] **Step 4: HIL** — `tools/hil_csi_rate.sh 30 80` → `PASS`. Если `FAIL` при `csi_hz` 30–80: записать в отчёт `rssi`, `drop_ratio` и попробовать `ping_hz set 150` — выход за рамки плана обсудить с пользователем, а не подбирать молча.
- [ ] **Step 5: Живая проверка** — `/device` во встроенном браузере: водопад реагирует, когда человек проходит между платой и узлом (попросить пользователя пройти; при отсутствии пользователя — отметить шаг как невыполненный).
- [ ] **Step 6: Отказоустойчивость** — `docker compose stop api` на 20 с, затем `start`: плата продолжает работать, поток возобновляется, `rejected_replay` не растёт. Перезагрузка платы (`reboot` в консоли) → поток возобновляется с новым `boot_id`.
- [ ] **Step 7: Длительный прогон S1** — `tools/hil_csi_rate.sh 600 80` → `PASS`.
- [ ] **Step 8: Commit** — `feat(fw): CSI capture, ping generator, signed UDP uplink, telemetry`

---

## Следующие планы (пишутся после завершения фазы 1)

- **Фаза 2 — съёмка и 3D:** TLS (Caddy + mkcert), `/capture` (WebXR, поза 30 Гц, углы, предметы, метки), сессии и хранение, синхронизация времени, система координат, `/` 3D (Three.js, локально без CDN), `/data`, Playwright smoke.
- **Фаза 3 — модель:** `shared/subcarriers.json`, `features.c`/`features.py` + golden-векторы, датасет, обучение Keras, int8-экспорт, TFLite Micro на плате, PRED-пакеты, разделы model_a/b, HTTPS-опрос и OTA с откатом, теневой инференс, `/model`, исправления и автодообучение, гейт деплоя.
