#pragma once
/* Сторож указателей «интерфейса» Wi-Fi в данных ПЗУ (0x3fcef81c..0x3fcef958: pp_wdev_funcs, net80211_funcs, ...).
 * Через них ходит весь код Wi-Fi в ПЗУ; все падения плат 13.10 (lmacProcessRxSucData, ppRecycleRxPkt,
 * ieee80211_output_do, вызов по адресу 0) похожи на порчу этих указателей или таблиц, на которые они указывают.
 * guard_arm: снимок + аппаратная точка останова на запись в pp_wdev_funcs на обоих ядрах (запись процессором
 * сразу роняет плату с обратной трассой писавшего — её видно в DIAG). guard_check: раз в секунду сравнить снимок
 * (ловит и запись DMA, которую точка останова не видит) и сообщить изменения через uplink_log. */
void guard_arm(void);
void guard_check(void);
/* Свободный стек (байт, минимум за загрузку) задач прошивки и системы — строкой через uplink_log. */
void guard_report_stacks(void);
/* Проверка внутренней кучи (нужна CONFIG_HEAP_POISONING_COMPREHENSIVE): при порче — отчёт через uplink_log и abort. */
void guard_heap_check(void);
