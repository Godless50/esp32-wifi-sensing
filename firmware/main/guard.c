#include "guard.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_ipc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "panic_trace.h"
#include "uplink.h"

#define ROMIF_START 0x3fcef81cu
#define ROMIF_END 0x3fcef958u
#define ROMIF_WORDS ((ROMIF_END - ROMIF_START) / 4)
#define PP_WDEV_FUNCS 0x3fcef944u
#define NET80211_FUNCS 0x3fcef858u
#define WORD(addr) (((addr) - ROMIF_START) / 4)

static uint32_t s_snap[ROMIF_WORDS];
static bool s_armed;

static void set_wp(void *arg)
{
    esp_cpu_set_watchpoint(0, (const void *)PP_WDEV_FUNCS, 4, ESP_CPU_WATCHPOINT_STORE);
}

void guard_arm(void)
{
    if (s_armed) return;
    memcpy(s_snap, (const void *)ROMIF_START, sizeof(s_snap));
    s_armed = true;
    /* Прошлая загрузка кончилась срабатыванием точки останова (паника DEBUG) — в этой её не ставим: если запись
     * законная и частая, плата иначе падала бы по кругу и не успевала бы принять исправленную прошивку. */
    uint8_t exc = 0;
    panic_trace_prev(NULL, NULL, &exc, NULL, NULL, 0);
    bool wp = exc != 1;
    if (wp) {
        esp_ipc_call_blocking(0, set_wp, NULL);
        esp_ipc_call_blocking(1, set_wp, NULL);
    }
    uplink_log("guard armed%s: pp_wdev_funcs=%08lx net80211_funcs=%08lx", wp ? " with watchpoint" : "",
               (unsigned long)s_snap[WORD(PP_WDEV_FUNCS)], (unsigned long)s_snap[WORD(NET80211_FUNCS)]);
}

void guard_check(void)
{
    if (!s_armed) return;
    const volatile uint32_t *w = (const volatile uint32_t *)ROMIF_START;
    int reported = 0;
    for (unsigned i = 0; i < ROMIF_WORDS; i++) {
        uint32_t v = w[i];
        if (v == s_snap[i]) continue;
        if (reported++ < 4)
            uplink_log("romif %08lx %08lx->%08lx", (unsigned long)(ROMIF_START + i * 4), (unsigned long)s_snap[i],
                       (unsigned long)v);
        s_snap[i] = v;
    }
}

void guard_report_stacks(void)
{
    static const char *const names[] = {"uplink", "ping_gen", "espnow_beacon", "model_ota", "infer", "sys_evt", "wifi",
                                        "tiT", "esp_timer", "ipc0", "ipc1", "console_wait", "console_repl", "main"};
    char line[200];
    int n = snprintf(line, sizeof(line), "stack free:");
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]) && n < (int)sizeof(line) - 24; i++) {
        TaskHandle_t h = xTaskGetHandle(names[i]);
        if (h) n += snprintf(line + n, sizeof(line) - n, " %s=%u", names[i], (unsigned)uxTaskGetStackHighWaterMark(h));
    }
    uplink_log("%s", line);
}

/* --- куча (CONFIG_HEAP_POISONING_COMPREHENSIVE): у занятых блоков метки до и после данных, свободные залиты 0xfe ---
 * Порча кучи видна по падениям 13.10 (tlsf_free, assert в pbuf_free). Проверка внутренней памяти раз в 2 с; при
 * порче — какой блок, его размер и содержимое, и соседний блок перед ним (обычно он и переполнился), потом abort:
 * запись о падении (DIAG) покажет и это сообщение. */
#define HEAD_CANARY 0xABBA1234u
#define TAIL_CANARY 0xBAAD5678u

typedef struct {
    const uint8_t *ptr;
    size_t size;
    bool used;
} blk_t;
static blk_t s_prev, s_bad, s_bad_prev;
static int s_bad_n;
static char s_why;

static bool walk_cb(walker_heap_into_t heap, walker_block_info_t b, void *arg)
{
    const uint8_t *p = (const uint8_t *)b.ptr;
    char why = 0;
    if (b.used && b.size >= 12) {
        uint32_t hc, n, tc;
        memcpy(&hc, p, 4);
        memcpy(&n, p + 4, 4);
        if (hc != HEAD_CANARY) why = 'h';
        else if (n + 12 > b.size) why = 's';
        else {
            memcpy(&tc, p + 8 + n, 4);
            if (tc != TAIL_CANARY) why = 't';
        }
    } else if (!b.used) {
        for (size_t i = 8; i + 8 <= b.size && i < 72; i += 4) {
            uint32_t w;
            memcpy(&w, p + i, 4);
            if (w != 0xfefefefeu) { why = 'f'; break; }
        }
    }
    if (why && s_bad_n++ == 0) {
        s_bad = (blk_t){p, b.size, b.used};
        s_bad_prev = s_prev;
        s_why = why;
    }
    s_prev = (blk_t){p, b.size, b.used};
    return true;
}

static void hex16(char *out, const uint8_t *p)
{
    for (int i = 0; i < 16; i++) sprintf(out + 2 * i, "%02x", p[i]);
}

void guard_heap_check(void)
{
#if CONFIG_HEAP_POISONING_COMPREHENSIVE
    if (heap_caps_check_integrity(MALLOC_CAP_INTERNAL, false)) return;
    s_bad_n = 0;
    s_prev = (blk_t){0};
    heap_caps_walk(MALLOC_CAP_INTERNAL, walk_cb, NULL);
    char a[33] = "", b[33] = "";
    if (s_bad_n) {
        hex16(a, s_bad.ptr);
        if (s_bad_prev.ptr && s_bad_prev.size >= 16) hex16(b, s_bad_prev.ptr + s_bad_prev.size - 16);
        uplink_log("HEAP CORRUPT %c at %p size %u %s: %s | prev %p size %u %s ends %s", s_why, s_bad.ptr,
                   (unsigned)s_bad.size, s_bad.used ? "used" : "free", a, s_bad_prev.ptr, (unsigned)s_bad_prev.size,
                   s_bad_prev.used ? "used" : "free", b);
        if (s_bad.used && s_bad.size >= 8 && s_why == 't') { /* хвост переполнен: что лежит за концом данных */
            uint32_t n;
            memcpy(&n, s_bad.ptr + 4, 4);
            if (n + 24 <= s_bad.size) {
                hex16(a, s_bad.ptr + 8 + n - 4);
                uplink_log("HEAP tail of %u-byte block: %s", (unsigned)n, a);
            }
        }
    } else {
        uplink_log("HEAP CORRUPT (integrity check failed, walk found nothing)");
    }
    vTaskDelay(pdMS_TO_TICKS(1500)); /* uplink успевает отправить строки */
    abort();
#endif
}
