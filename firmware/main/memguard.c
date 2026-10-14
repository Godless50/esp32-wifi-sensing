/* Guard against a bug of the closed Wi-Fi library: with the sniffer on, wDev_SnifferRxData sometimes copies a
 * received frame with a negative length (memcpy with -156, 13.10; again on 14.10 with A-MPDU reception off and the
 * MPDU-only filter: "Cache disabled but cached memory region accessed" after 12 min). Every memcpy of the firmware
 * (the library's too) goes through here (-Wl,--wrap=memcpy in CMakeLists.txt): a length above 2 GB is never a
 * real copy on this chip (512 KB of RAM, 8 MB of PSRAM) — it is skipped and counted instead of running through
 * the whole address space. In IRAM: memcpy is called with the flash cache disabled too. */
#include <stddef.h>
#include <stdint.h>

#include "esp_attr.h"
#include "memguard.h"

void *__real_memcpy(void *dst, const void *src, size_t n);

static volatile uint32_t s_skipped;
static volatile uint32_t s_last_caller;

IRAM_ATTR void *__wrap_memcpy(void *dst, const void *src, size_t n)
{
    if (__builtin_expect(n > 0x7FFFFFFFu, 0)) {
        s_skipped++;
        s_last_caller = (uint32_t)__builtin_return_address(0);
        return dst;
    }
    return __real_memcpy(dst, src, n);
}

uint32_t memguard_skipped(uint32_t *last_caller)
{
    if (last_caller) *last_caller = s_last_caller;
    return s_skipped;
}
