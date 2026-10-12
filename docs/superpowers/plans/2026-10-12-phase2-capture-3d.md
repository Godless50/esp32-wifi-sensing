# Фаза 2: съёмка с телефона, сессии, план квартиры, 3D — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пользователь ходит по квартире с Android-телефоном (Chrome, WebXR), сервер синхронно пишет CSI платы и
позу телефона в сессии, из отмеченных углов строится план квартиры в общей системе координат, страница `/`
показывает 3D-сцену (стены, предметы, плата, узел, траектории, живая позиция телефона и индикатор движения по CSI).

**Architecture:** Caddy (TLS на 8443, внутренний CA Caddy, basic auth) → api (FastAPI). Новые модули пакета `wsn`:
`store` (SQLite + файлы сессий), `recorder` (запись сырых датаграмм активной сессии), `timesync` (смещение часов
телефона), `geometry` (система координат по двум точкам, полигоны), `capture` (WS-протокол телефона), `plan` (API плана).
UI: `/capture` (WebXR immersive-ar + DOM overlay), `/` (Three.js, локальная копия), `/data`.

**Tech Stack:** как в фазе 1 + `caddy:2-alpine`, SQLite (stdlib `sqlite3`), Three.js (вендорится в `ui/vendor/`),
Playwright (образ `mcr.microsoft.com/playwright/python`) для smoke-тестов.

**Spec:** `docs/superpowers/specs/2026-10-12-wifi-sensing-design.md` — разделы 6.2, 6.3, 6.4, 7 (`/`, `/capture`, `/data`), 8 (UI smoke).

## Global Constraints

- Всё из Global Constraints фазы 1 остаётся в силе (порты, протокол, Docker, коммиты с трейлерами).
- Внешний доступ только `https://192.168.4.77:8443` (Caddy, `tls internal`, сертификат на IP); api слушает 8080 только на 127.0.0.1 и в сети compose. UDP 5005 без изменений.
- Basic auth на 8443: пользователь `wsn`, пароль генерируется один раз в `data/ui_password` (0600), хеш bcrypt — в `data/caddy.env`. Корневой сертификат CA отдаётся без авторизации по `/ca.crt`.
- Время: все серверные отметки — `time.monotonic()` процесса api (как `recv_t` CSI). Телефон шлёт время в своей шкале `performance.now()`; сервер переводит его в свою по оценке смещения.
- Сессии: `data/sessions/<id>/csi.bin` (записи `u64 recv_ns · u16 len · bytes` — сырые датаграммы), `pose.jsonl`, `events.jsonl`, `meta.json`; метаданные — SQLite `data/wsn.db`.
- Одновременно активна не более одной сессии записи.
- Система координат плана (спец. 6.4): начало — плата, ось +X — на узел mesh, +Y — влево от неё (правая тройка с вертикалью вверх). Горизонтальная проекция позы WebXR: `(x, −z)`.
- UI без CDN: все библиотеки лежат в `ui/vendor/`.
- Мусорная мелочь фазы 1 M6 (API без авторизации в LAN) закрывается задачей 1.

## Review Focus

1. Телефон теряет связь посреди сессии (Wi-Fi, блокировка экрана): позы буферизуются на телефоне и досылаются; сервер не дублирует уже принятые (тест в Task 3).
2. Пользователь забыл отметить «роутер» или отметил его в той же точке, что и «старт»: план не строится из такой сессии, UI говорит почему, сервер не падает (тест в Task 4).
3. Самопересекающийся или вырожденный полигон комнаты (< 3 углов, нулевая площадь): комната не сохраняется, ошибка видна на телефоне (тест в Task 4).
4. Сессия записи не остановлена (вкладка закрыта): сессия автоматически закрывается через 120 с без поз и без CSI (тест в Task 2).
5. Две вкладки `/capture` одновременно: вторая получает отказ «сессия уже идёт», данные первой не портятся (тест в Task 3).

---

### Task 1: TLS, авторизация, сертификат для телефона, проверка WebXR на устройстве

**Files:**
- Create: `deploy/Caddyfile`, `tools/ui_password.sh`, `ui/capture/probe.html`
- Modify: `deploy/docker-compose.yml`

**Interfaces:**
- Produces: `https://192.168.4.77:8443/*` → api; `GET /ca.crt` (без auth) — корневой сертификат локального CA Caddy; `/capture/probe` — страница-проба: показывает `isSecureContext`, `navigator.xr`, `isSessionSupported('immersive-ar')`, по кнопке запускает сессию с `hit-test` и `dom-overlay`, выводит позу и найденную точку пола.

- [ ] **Step 1:** `Caddyfile`: сайт `https://192.168.4.77:8443` с `tls internal`; `handle /ca.crt` → файл `/data/caddy/pki/authorities/local/root.crt` с `Content-Type: application/x-x509-ca-cert`; остальное — `basic_auth` (`wsn` / `{$WSN_UI_HASH}`) + `reverse_proxy api:8080`. Compose: сервис `caddy` (`caddy:2-alpine`, порт `8443:8443`, том `caddy_data`, env_file `../data/caddy.env`), api: порт `127.0.0.1:8080:8080`.
- [ ] **Step 2:** `tools/ui_password.sh` — если нет `data/ui_password`, генерирует 20 символов `[A-Za-z0-9]`, пишет 0600, строит хеш `docker run --rm caddy:2-alpine caddy hash-password --plaintext …` и пишет `WSN_UI_HASH=…` в `data/caddy.env` (0600). Повторный запуск ничего не меняет.
- [ ] **Step 3: проверка** — `curl -sk https://127.0.0.1:8443/api/devices` → 401; с `-u wsn:<пароль>` → 200; `curl -sk https://127.0.0.1:8443/ca.crt | openssl x509 -noout -subject` выводит subject CA; `curl -s http://192.168.4.77:8080/` с другого интерфейса недоступен (connection refused).
- [ ] **Step 4: проба на телефоне (нужен пользователь)** — пользователь ставит CA на телефон (Настройки → Безопасность → Шифрование и учётные данные → Установить сертификат → Сертификат ЦС), открывает `https://192.168.4.77:8443/capture/probe`. Ожидается: `immersive-ar: supported`, после «Старт» позы меняются при движении, hit-test находит пол. Если WebXR не поддерживается — остановиться и обсудить с пользователем (фазу 2 без WebXR надо перепроектировать).
- [ ] **Step 5: Commit** — `feat(deploy): TLS via Caddy internal CA, basic auth, WebXR probe page`

---

### Task 2: Хранилище и запись сессий

**Files:**
- Create: `server/wsn/store.py`, `server/wsn/recorder.py`
- Modify: `server/wsn/__main__.py`, `server/wsn/app.py`
- Test: `server/tests/test_store.py`, `server/tests/test_recorder.py`

**Interfaces:**
- Produces:
  - `class Store: __init__(db_path: Path, sessions_dir: Path)`; `start_session(kind: Literal["walk","empty"], device_id: str, now: float) -> str` (id `YYYYmmdd-HHMMSS-<4 hex>`; бросает `SessionBusy`, если есть активная); `stop_session(sid: str, now: float) -> None`; `active() -> SessionInfo | None`; `list_sessions() -> list[SessionInfo]`; `delete_session(sid) -> None` (активную удалить нельзя → `SessionBusy`); `session_dir(sid) -> Path`.
  - `@dataclass SessionInfo: id, kind, device_id, started, stopped: float|None, csi_packets: int, poses: int, events: int, plan_ok: bool`
  - `class Recorder: __init__(store: Store, idle_timeout_s: float = 120.0)`; `on_datagram(buf: bytes, device_id: str, recv_t: float) -> None` (пишет, только если сессия активна и `device_id` совпадает); `tick(now: float) -> None` (авто-стоп по бездействию: нет ни поз, ни CSI 120 с); `note_activity(now: float)`.
  - `IngestService` получает необязательный `on_raw: Callable[[bytes, str, float], None]`, вызываемый только для принятых пакетов.
  - REST: `POST /api/sessions {kind, device_id}` → `SessionInfo`; `POST /api/sessions/{id}/stop`; `GET /api/sessions`; `DELETE /api/sessions/{id}`.
- [ ] **Step 1: падающие тесты** — старт/стоп/список; второй старт → `SessionBusy`; `csi.bin` после 3 датаграмм читается функцией `read_csi_bin(path) -> list[tuple[float, bytes]]` с теми же байтами и временем (нс → с); датаграммы чужого устройства и вне сессии не пишутся; `tick(now=start+121)` без активности закрывает сессию, `tick(now=start+60)` — нет; удаление активной → `SessionBusy`, неактивной — каталог исчезает.
- [ ] **Step 2: FAIL → реализовать → PASS** (`pytest -q`). Файлы пишутся с `flush()` на каждую датаграмму и `fsync` при стопе.
- [ ] **Step 3: Commit** — `feat(server): sessions store and raw CSI recorder`

---

### Task 3: Протокол телефона и синхронизация времени

**Files:**
- Create: `server/wsn/timesync.py`, `server/wsn/capture.py`
- Modify: `server/wsn/app.py`
- Test: `server/tests/test_timesync.py`, `server/tests/test_capture.py`

**Interfaces:**
- Produces:
  - `class ClockSync: add(c0: float, s: float, c1: float) -> None` (c0/c1 — время телефона отправки/приёма pong, s — время сервера); `offset -> float | None` (сервер − телефон по выборке с минимальным RTT из последних 20); `to_server(t_phone: float) -> float`.
  - WS `/ws/capture` (JSON): клиент → `{"type":"hello","client_id":str}`, `{"type":"ping","c0":ms}`, `{"type":"pose","seq":int,"t":ms,"p":[x,y,z],"q":[x,y,z,w]}` (пачками: `{"type":"poses","items":[…]}`), `{"type":"event","seq":int,"t":ms,"kind":…, …}`; сервер → `{"type":"pong","c0":ms,"s":sec}`, `{"type":"ack","seq":int}` (последний сохранённый seq), `{"type":"state","session":SessionInfo|null,"csi_hz":float,"plan":{…}}` раз в 1 с, `{"type":"error","code":str,"message":str}`.
  - `kind` событий: `session_start` (`session_kind`: walk|empty), `session_stop`, `origin`, `router`, `room_new` (`name`), `corner` (`p`, источник `hit|phone`), `room_close`, `object` (`p`, `type`, `size`), `label` (`pose`: stand|sit|lie|walk|none).
  - Владелец сессии — один `client_id`; второй клиент получает `error` `session_busy` на `session_start`.
- [ ] **Step 1: падающие тесты** — `ClockSync`: при выборках с RTT 5/50/200 мс выбирается RTT 5 мс, `offset` точен до 3 мс; без выборок `to_server` бросает `ValueError`. WS: hello → ping/pong; `session_start` создаёт сессию; позы с seq 1..5, затем повтор seq 3..7 → в `pose.jsonl` ровно 7 строк, `ack` = 7; вторая вкладка `session_start` → `session_busy`; `session_stop` закрывает сессию; время поз в файле — в шкале сервера.
- [ ] **Step 2: FAIL → реализовать → PASS**
- [ ] **Step 3: Commit** — `feat(server): phone capture websocket with clock sync and dedup`

---

### Task 4: Геометрия и план квартиры

**Files:**
- Create: `server/wsn/geometry.py`, `server/wsn/plan.py`
- Modify: `server/wsn/capture.py`, `server/wsn/app.py`
- Test: `server/tests/test_geometry.py`, `server/tests/test_plan.py`

**Interfaces:**
- Produces:
  - `xr_to_floor(p: tuple[float,float,float]) -> tuple[float,float]` = `(x, −z)`.
  - `@dataclass Frame: origin: (float,float); angle: float`; `frame_from_points(origin_xr, router_xr) -> Frame` (бросает `GeometryError("router too close")`, если расстояние < 0.5 м); `Frame.apply(p_xr) -> (X, Y)` в метрах.
  - `polygon_valid(pts) -> str | None` (None = ок; иначе причина: `"too few corners"`, `"zero area"`, `"self-intersecting"`); `point_in_polygon(pt, poly) -> bool`.
  - `build_plan(store) -> Plan` — по всем завершённым сессиям с `origin`+`router`: комнаты (последнее определение комнаты с тем же именем побеждает), предметы, траектории (`[[t,X,Y], …]` с прореживанием до 5 Гц), `router_xy`, сессии без кадра — в `skipped` с причиной.
  - `GET /api/plan` → `{"rooms":[{"name","polygon":[[X,Y],…]}],"objects":[{"type","xy","size"}],"board":[0,0],"router":[X,Y],"trajectories":{sid:[[t,X,Y],…]},"skipped":{sid:reason}}`.
  - При `room_close` сервер проверяет `polygon_valid` и отвечает `error` `bad_polygon` с причиной.
- [ ] **Step 1: падающие тесты** — `frame_from_points((0,0,0),(0,0,-3))`: роутер → `(3,0)`; точка `(-1,0,0)` (влево) → `(0,1)`; поворот и сдвиг произвольного кадра сохраняют расстояния; роутер в 0.3 м → `GeometryError`; `polygon_valid` для 2 точек, коллинеарных, «бабочки», квадрата; `build_plan` на двух синтетических сессиях с разными начальными позами телефона даёт одинаковые координаты одного и того же угла (±1 см); сессия без `router` попадает в `skipped`.
- [ ] **Step 2: FAIL → реализовать → PASS**
- [ ] **Step 3: Commit** — `feat(server): floor-plan geometry and /api/plan`

---

### Task 5: Страница съёмки `/capture`

**Files:**
- Create: `ui/capture/index.html`, `ui/capture/capture.js`
- Modify: `server/wsn/app.py` (маршрут `/capture`)

**Interfaces:**
- Consumes: WS `/ws/capture` (Task 3).
- Поведение: без `immersive-ar` — понятное сообщение и ссылка на `/capture/probe`. Сессия XR `immersive-ar` с `requiredFeatures: ["local-floor","hit-test"]`, `optionalFeatures: ["dom-overlay"]`. Позы отправляются с частотой 30 Гц пачками раз в 200 мс; неподтверждённые хранятся в памяти (до 10 мин) и досылаются после переподключения. Ping раз в 5 с. Кнопки (крупные, для одной руки): «Старт записи / Пусто / Стоп», «Я у платы» (`origin`), «Я у роутера» (`router`), «Новая комната» (имя), «Угол» (по hit-test в центре экрана; если пола не видно — по позе телефона, с пометкой), «Закрыть комнату», «Предмет» (тип из списка), позы «Стою / Сижу / Лежу / Иду». Статус: запись, длительность, CSI Гц, неподтверждённые позы, ошибки сервера.
- [ ] **Step 1:** реализовать страницу; проверка в десктопном браузере: без WebXR показывается сообщение, кнопки неактивны, WS подключается (статус «онлайн»).
- [ ] **Step 2: проверка на телефоне (нужен пользователь)** — короткая сессия: старт у платы, роутер, одна комната из 4 углов, стоп. Ожидается: в `/api/plan` одна комната с 4 углами, `skipped` пуст.
- [ ] **Step 3: Commit** — `feat(ui): WebXR capture page`

---

### Task 6: 3D-сцена `/`

**Files:**
- Create: `ui/vendor/three.module.min.js` (+ `OrbitControls.js`), `ui/scene/index.html`, `ui/scene/scene.js`, `tools/vendor_three.sh`
- Modify: `server/wsn/app.py` (`/` → сцена), `server/wsn/live.py` (индикатор движения)

**Interfaces:**
- Produces: `LiveHub` сообщение `{"type":"motion","device_id","t","energy":float}` с частотой 4 Гц: `energy` = среднее по поднесущим стандартного отклонения амплитуды HT-LTF за последнюю 1 с, нормированное на медиану за последние 60 с (≈1 в покое). Функция `motion_energy(amps: np.ndarray[T,64]) -> float` тестируется.
- Сцена: пол и стены (экструзия полигонов, 2.6 м), предметы-боксы с подписью, маркеры платы и роутера, траектории сессий (переключатель), живая позиция телефона во время съёмки (из `state` капчура через `/ws/live`), полоса «движение» по `energy`. Камера Orbit, кнопки «сверху / перспектива».
- [ ] **Step 1: падающий тест** `motion_energy`: постоянный сигнал → 0; шум σ=1 по всем поднесущим → ≈1 (±10 %); `test_live` — хаб шлёт `motion` не чаще 4 Гц.
- [ ] **Step 2: FAIL → реализовать → PASS;** `tools/vendor_three.sh` скачивает `three@0.169.0` через `docker run node:22-alpine npm pack` и копирует нужные файлы.
- [ ] **Step 3: проверка** — синтетический план (фикстура сессии из Task 4) отображается: скриншот встроенного браузера.
- [ ] **Step 4: Commit** — `feat(ui): 3D scene with plan, trajectories and motion indicator`

---

### Task 7: Страница `/data` и воспроизведение сессий

**Files:**
- Create: `ui/data/index.html`, `ui/data/data.js`
- Modify: `server/wsn/replay.py` (`--from-file csi.bin`), `server/wsn/app.py`
- Test: `server/tests/test_replay.py`

- [ ] **Step 1: падающий тест** — `replay_file(path, speed) -> Iterator[tuple[float, bytes]]` отдаёт датаграммы из `csi.bin` с исходными интервалами, делёнными на `speed`.
- [ ] **Step 2: FAIL → реализовать → PASS**
- [ ] **Step 3:** `/data`: таблица сессий (тип, начало, длительность, CSI-пакетов, поз, событий, «план ок» / причина), удаление с подтверждением в интерфейсе, ссылка «скачать архив» (`GET /api/sessions/{id}/archive` → tar.gz).
- [ ] **Step 4: Commit** — `feat(ui): sessions page, archive download, file replay`

---

### Task 8: Smoke-тесты UI

**Files:**
- Create: `tests_ui/test_smoke.py`, `tools/ui_smoke.sh`

- [ ] **Step 1:** `tools/ui_smoke.sh` поднимает стек, запускает `replay` синтетики и Playwright (`mcr.microsoft.com/playwright/python:v1.48.0-jammy`, `--network host`, `ignore_https_errors`, basic auth). Тесты: `/device` — водопад получает ≥ 10 строк за 3 с; `/` — канвас отрисован, нет ошибок в консоли; `/data` — таблица содержит фикстурную сессию; `/capture` в десктопном Chromium — сообщение об отсутствии WebXR, WS «онлайн».
- [ ] **Step 2:** запуск → PASS.
- [ ] **Step 3: Commit** — `test(ui): Playwright smoke for all pages`
