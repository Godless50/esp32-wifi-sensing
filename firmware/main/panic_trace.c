#include "panic_trace.h"

#include <string.h>

#include "esp_attr.h"
#include "esp_cpu.h"
#include "esp_cpu_utils.h"
#include "esp_debug_helpers.h"
#include "esp_memory_utils.h"
#include "esp_private/panic_internal.h"
#include "esp_system.h"
#include "xtensa_context.h"

#define PT_MAGIC 0x57534e50u /* "WSNP" */
#define PT_MSG 48
#define PT_BT 8

typedef struct {
    uint32_t up_magic, up_s;
    uint32_t p_magic, fw, pc, reason_ptr;
    uint8_t exc, core, rsv[2];
    char msg[PT_MSG];
    uint32_t e_magic, e_pc;  /* вход в вектор исключения: пишется раньше всего остального кода паники */
    uint8_t e_kind, e_cause, e_core, bt_n;
    uint32_t bt[PT_BT];
} pt_t;

static RTC_NOINIT_ATTR pt_t s_rtc;
static DRAM_ATTR uint32_t s_fw;
static uint32_t s_prev_up, s_prev_pc;
static uint8_t s_prev_exc, s_prev_core;
static char s_prev_msg[PT_MSG];
static uint32_t s_prev_epc;
static uint8_t s_prev_ekind, s_prev_ecause, s_prev_bt_n;
static uint32_t s_prev_bt[PT_BT];

/* Строку копируем при панике только из внутренней RAM: флеш может быть недоступен (Cache error). Строки во флеше
 * (обычные причины исключений) берём после перезапуска по сохранённому адресу, если прошивка та же. */
static void IRAM_ATTR copy_dram(char *dst, const char *src)
{
    size_t i = 0;
    if (src && esp_ptr_in_dram(src))
        for (; i < PT_MSG - 1 && src[i]; i++) dst[i] = src[i];
    dst[i] = 0;
}

/* Самая ранняя точка: обработчики, которые вызывает ассемблерный код векторов (--wrap=panicHandler,
 * --wrap=xt_unhandled_exception). Если сам обработчик паники потом зависнет (сброс сторожем RTC через 10 с),
 * останется хотя бы адрес и причина. */
static inline void IRAM_ATTR early(void *frame, uint8_t kind)
{
    if (s_rtc.e_magic == PT_MAGIC) return;
    const XtExcFrame *f = (const XtExcFrame *)frame;
    s_rtc.e_pc = f ? (uint32_t)f->pc : 0;
    s_rtc.e_cause = f ? (uint8_t)f->exccause : 0xff;
    s_rtc.e_kind = kind;
    s_rtc.e_core = (uint8_t)esp_cpu_get_core_id();
    s_rtc.e_magic = PT_MAGIC;
}

extern void __real_panicHandler(void *frame);
extern void __real_xt_unhandled_exception(void *frame);

void IRAM_ATTR __wrap_panicHandler(void *frame)
{
    early(frame, 1);
    __real_panicHandler(frame);
}

void IRAM_ATTR __wrap_xt_unhandled_exception(void *frame)
{
    early(frame, 2);
    __real_xt_unhandled_exception(frame);
}

/* Обратная трасса так же, как её печатает ESP-IDF (esp_backtrace_print_from_frame). */
static void IRAM_ATTR capture_bt(const void *frame)
{
    const XtExcFrame *f = (const XtExcFrame *)frame;
    s_rtc.bt_n = 0;
    if (!f) return;
    esp_backtrace_frame_t b = {.pc = (uint32_t)f->pc, .sp = (uint32_t)f->a1, .next_pc = (uint32_t)f->a0,
                               .exc_frame = f};
    uint8_t n = 0;
    s_rtc.bt[n++] = esp_cpu_process_stack_pc(b.pc);
    while (n < PT_BT && b.next_pc != 0 && esp_stack_ptr_is_sane(b.sp)) {
        if (!esp_backtrace_get_next_frame(&b)) break;
        s_rtc.bt[n++] = esp_cpu_process_stack_pc(b.pc);
    }
    s_rtc.bt_n = n;
}

extern void __real_esp_panic_handler(panic_info_t *info);

/* Обёртка над обработчиком паники ESP-IDF (ключ компоновщика --wrap=esp_panic_handler). */
void IRAM_ATTR __wrap_esp_panic_handler(panic_info_t *info)
{
    if (s_rtc.p_magic != PT_MAGIC) { /* только первая паника загрузки */
        s_rtc.fw = s_fw;
        s_rtc.pc = (uint32_t)info->addr;
        s_rtc.core = (uint8_t)info->core;
        if (g_panic_abort) {
            s_rtc.exc = PANIC_EXCEPTION_ABORT + 1;
            s_rtc.reason_ptr = 0;
            copy_dram(s_rtc.msg, g_panic_abort_details);
        } else {
            s_rtc.exc = (uint8_t)(info->exception + 1);
            s_rtc.reason_ptr = (uint32_t)info->reason;
            copy_dram(s_rtc.msg, info->reason);
        }
        s_rtc.p_magic = PT_MAGIC;
        capture_bt(info->frame); /* после записи причины: если трасса упадёт, причина уже сохранена */
    }
    __real_esp_panic_handler(info);
}

void panic_trace_boot(uint32_t fw)
{
    s_fw = fw;
    if (esp_reset_reason() != ESP_RST_POWERON) {
        if (s_rtc.up_magic == PT_MAGIC) s_prev_up = s_rtc.up_s;
        if (s_rtc.p_magic == PT_MAGIC && s_rtc.exc >= 1 && s_rtc.exc <= PANIC_EXCEPTION_FAULT + 1) {
            s_prev_pc = s_rtc.pc;
            s_prev_exc = s_rtc.exc;
            s_prev_core = s_rtc.core;
            memcpy(s_prev_msg, s_rtc.msg, PT_MSG);
            s_prev_msg[PT_MSG - 1] = 0;
            s_prev_bt_n = s_rtc.bt_n <= PT_BT ? s_rtc.bt_n : 0;
            memcpy(s_prev_bt, s_rtc.bt, sizeof(s_prev_bt));
            const char *r = (const char *)s_rtc.reason_ptr;
            if (!s_prev_msg[0] && r && s_rtc.fw == fw && esp_ptr_in_drom(r)) {
                size_t i = 0;
                for (; i < PT_MSG - 1 && r[i]; i++) s_prev_msg[i] = r[i];
                s_prev_msg[i] = 0;
            }
        }
        if (s_rtc.e_magic == PT_MAGIC && (s_rtc.e_kind == 1 || s_rtc.e_kind == 2)) {
            s_prev_ekind = s_rtc.e_kind;
            s_prev_ecause = s_rtc.e_cause;
            s_prev_epc = s_rtc.e_pc;
            if (!s_prev_exc) s_prev_core = s_rtc.e_core;
        }
    }
    memset(&s_rtc, 0, sizeof(s_rtc));
}

void panic_trace_tick(uint32_t up_s)
{
    s_rtc.up_s = up_s;
    s_rtc.up_magic = PT_MAGIC;
}

void panic_trace_prev(uint32_t *up_s, uint32_t *pc, uint8_t *exc, uint8_t *core, char *msg, size_t cap)
{
    if (up_s) *up_s = s_prev_up;
    if (pc) *pc = s_prev_pc;
    if (exc) *exc = s_prev_exc;
    if (core) *core = s_prev_core;
    if (msg && cap) {
        strncpy(msg, s_prev_msg, cap - 1);
        msg[cap - 1] = 0;
    }
}

void panic_trace_early(uint8_t *kind, uint8_t *cause, uint32_t *pc)
{
    *kind = s_prev_ekind;
    *cause = s_prev_ecause;
    *pc = s_prev_epc;
}

uint8_t panic_trace_bt(uint32_t *bt, uint8_t cap)
{
    uint8_t n = s_prev_bt_n < cap ? s_prev_bt_n : cap;
    memcpy(bt, s_prev_bt, n * sizeof(uint32_t));
    return n;
}
