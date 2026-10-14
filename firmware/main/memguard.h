#pragma once
#include <stdint.h>

/* How many memcpy calls with an impossible (negative) length were skipped since boot, and who made the last. */
uint32_t memguard_skipped(uint32_t *last_caller);
