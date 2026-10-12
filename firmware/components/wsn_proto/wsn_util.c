#include "wsn_util.h"

#include <stdio.h>
#include <string.h>

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int wsn_parse_mac(const char *s, uint8_t out[6])
{
    if (!s || strlen(s) != 17) return -1;
    for (int i = 0; i < 6; i++) {
        int hi = hexval(s[3 * i]), lo = hexval(s[3 * i + 1]);
        if (hi < 0 || lo < 0) return -1;
        if (i < 5 && s[3 * i + 2] != ':') return -1;
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return 0;
}

int wsn_parse_hex(const char *s, uint8_t *out, size_t n)
{
    if (!s || strlen(s) != 2 * n) return -1;
    for (size_t i = 0; i < n; i++) {
        int hi = hexval(s[2 * i]), lo = hexval(s[2 * i + 1]);
        if (hi < 0 || lo < 0) return -1;
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return 0;
}

int wsn_valid_wpa_pass(const char *s)
{
    if (!s) return 0;
    size_t n = strlen(s);
    if (n == 0 || (n >= 8 && n <= 63)) return 1;
    if (n != 64) return 0;
    for (size_t i = 0; i < n; i++)
        if (hexval(s[i]) < 0) return 0;
    return 1;
}

void wsn_format_mac(const uint8_t mac[6], char out[18])
{
    snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}
