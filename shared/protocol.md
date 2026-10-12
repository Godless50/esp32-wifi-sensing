# Протокол WSN1 (UDP 5005, little-endian)

Источник истины: `server/wsn/protocol.py` и `firmware/components/wsn_proto`. Сверяются тестом `server/tests/test_c_golden.py`.

## Заголовок (21 байт)

| Смещение | Поле | Тип | Примечание |
|---|---|---|---|
| 0 | magic | char[4] | `"WSN1"` |
| 4 | ver | u8 | 1 |
| 5 | type | u8 | 1=CSI, 2=PRED (фаза 3), 3=TELEM |
| 6 | device_id | u8[6] | MAC STA платы |
| 12 | boot_id | u32 | случайный при загрузке |
| 16 | seq | u32 | с 0 после загрузки, +1 на пакет |
| 20 | n | u8 | число записей |

Затем полезная нагрузка, затем `hmac` — первые 16 байт HMAC-SHA256(key, всё предыдущее).
Датаграмма ≤ 1400 байт.

## CSI-кадр (14 байт + len), type=1, n ≤ 10

| Поле | Тип |
|---|---|
| t_us | u64 (esp_timer) |
| rssi | i8 |
| noise | i8 |
| sig_mode | u8 (0 = non-HT, 1 = HT) |
| cwb | u8 (0 = 20 МГц) |
| len | u16 |
| data | i8[len] — пары `imag, real` по поднесущим, как отдаёт ESP-IDF |

## TELEM (30 байт), type=3, n=1

`csi_hz f32 · drop_ratio f32 · rssi_avg i8 · heap_free u32 · psram_free u32 · uptime_s u32 · fw_ver u32 · model_ver u32 · wifi_state u8`

`wifi_state`: 0=IDLE, 1=CONNECTING, 2=CONNECTED, 3=BSSID_NOT_FOUND, 4=AUTH_FAIL.

## Приём на сервере

Пакет отбрасывается: неизвестный MAC; неверный HMAC; порча формата; `seq` ≤ последнего в текущем `boot_id`;
`boot_id` уже сменённой загрузки.
