// Small pure-C parsers used by the console (host-testable).
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* "aa:bb:cc:dd:ee:ff" (any case) -> 6 bytes. 0 on success. */
int wsn_parse_mac(const char *s, uint8_t out[6]);
/* Exactly 2*n hex digits -> n bytes. 0 on success. */
int wsn_parse_hex(const char *s, uint8_t *out, size_t n);
/* 6 bytes -> "aa:bb:cc:dd:ee:ff" (out >= 18 bytes). */
void wsn_format_mac(const uint8_t mac[6], char out[18]);

#ifdef __cplusplus
}
#endif
