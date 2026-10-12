// Host tests for wsn_util parsers.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "wsn_util.h"

int main(void)
{
    uint8_t mac[6];
    assert(wsn_parse_mac("02:00:00:00:a1:18", mac) == 0);
    const uint8_t want[6] = {0x02, 0x00, 0x00, 0x00, 0xa1, 0x18};
    assert(memcmp(mac, want, 6) == 0);
    assert(wsn_parse_mac("02:00:00:00:a1:18", mac) == 0);
    assert(wsn_parse_mac("02:00:00:00:a1", mac) != 0);
    assert(wsn_parse_mac("02:00:00:00:a1:18:00", mac) != 0);
    assert(wsn_parse_mac("zz:17:0e:de:db:18", mac) != 0);
    assert(wsn_parse_mac("", mac) != 0);

    uint8_t key[32];
    char hex[65];
    for (int i = 0; i < 32; i++) sprintf(hex + 2 * i, "%02x", i);
    assert(wsn_parse_hex(hex, key, 32) == 0);
    for (int i = 0; i < 32; i++) assert(key[i] == i);
    assert(wsn_parse_hex("00", key, 32) != 0);          /* too short */
    hex[5] = 'g';
    assert(wsn_parse_hex(hex, key, 32) != 0);           /* bad digit */

    char out[18];
    wsn_format_mac(want, out);
    assert(strcmp(out, "02:00:00:00:a1:18") == 0);
    puts("OK util");
    return 0;
}
