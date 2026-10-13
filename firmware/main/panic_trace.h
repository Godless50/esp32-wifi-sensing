#pragma once
/* Причина падения и время работы прошлой загрузки — в RTC-памяти (переживает перезапуск после паники и
 * сторожевого таймера, не переживает пропадание питания). Без записи во флеш: можно во время приёма CSI. */
#include <stddef.h>
#include <stdint.h>

/* Вызывать первым делом при старте: забирает данные прошлой загрузки и очищает их. fw = wsn_fw_id(). */
void panic_trace_boot(uint32_t fw);
/* Раз в секунду: сколько секунд работает текущая загрузка. */
void panic_trace_tick(uint32_t up_s);
/* Данные прошлой загрузки. exc 0 = не падала. msg — строка причины (может быть пустой). */
void panic_trace_prev(uint32_t *up_s, uint32_t *pc, uint8_t *exc, uint8_t *core, char *msg, size_t cap);
/* Вход прошлой загрузки в вектор исключения (даже если обработчик паники потом умер): kind 0 = не было,
 * 1 = прерывание (IWDT, ошибка кэша; cause = PANIC_RSN_*), 2 = исключение CPU (cause = EXCCAUSE). */
void panic_trace_early(uint8_t *kind, uint8_t *cause, uint32_t *pc);
/* Обратная трасса последней паники прошлой загрузки (адреса как в выводе ESP-IDF). Возвращает их число. */
uint8_t panic_trace_bt(uint32_t *bt, uint8_t cap);
