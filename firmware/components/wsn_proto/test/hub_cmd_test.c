/* Parses server/tests/golden/py_hub_cmd.hex (made by wsn.protocol.encode_hub / encode_cmd) with the C parsers:
 * line 1 = hub announcement, line 2 = survey command, line 3 = links command. */
#include <stdio.h>
#include <string.h>

#include "hmac_openssl.h"
#include "wsn_proto.h"

#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)

static size_t unhex(const char *hex, uint8_t *buf)
{
    size_t n = 0;
    for (const char *p = hex; p[0] && p[1] && p[0] != '\n'; p += 2) {
        unsigned v;
        sscanf(p, "%2x", &v);
        buf[n++] = (uint8_t)v;
    }
    return n;
}

int main(int argc, char **argv)
{
    FILE *f = fopen(argc > 1 ? argv[1] : "../../../../server/tests/golden/py_hub_cmd.hex", "r");
    CHECK(f);
    static char hex[3][2048];
    for (int i = 0; i < 3; i++) CHECK(fgets(hex[i], sizeof(hex[i]), f));
    fclose(f);
    uint8_t key[32];
    for (int i = 0; i < 32; i++) key[i] = (uint8_t)i;
    wsn_ctx_t c = {.device_id = {0x02, 0, 0, 0, 0, 0x01}, .boot_id = 0xDEADBEEF, .key = key, .key_len = 32,
                   .hmac = wsn_hmac_openssl};
    uint8_t buf[2048];
    uint32_t seq = 0;

    size_t n = unhex(hex[0], buf);
    wsn_hub_t hub;
    CHECK(wsn_parse_hub(&c, buf, n, &seq, &hub) == 0);
    CHECK(seq == 1792000000u && hub.ip[0] == 192 && hub.ip[1] == 168 && hub.ip[2] == 4 && hub.ip[3] == 23);
    CHECK(hub.port == 5005);
    wsn_cmd_t cmd;
    CHECK(wsn_parse_cmd(&c, buf, n, &seq, &cmd) == -4);  /* a hub announcement is not a command */
    buf[n - 1] ^= 1;
    CHECK(wsn_parse_hub(&c, buf, n, &seq, &hub) == -3);  /* signature */
    buf[n - 1] ^= 1;
    c.device_id[5] = 2;
    CHECK(wsn_parse_hub(&c, buf, n, &seq, &hub) == -5);  /* another board */
    c.device_id[5] = 1;

    n = unhex(hex[1], buf);
    CHECK(wsn_parse_cmd(&c, buf, n, &seq, &cmd) == 0);
    CHECK(seq == 9 && cmd.cmd == WSN_CMD_SURVEY && cmd.len == 3 + 4 * 2);
    CHECK(cmd.args[0] == 60 && cmd.args[1] == 6 && cmd.args[2] == 2);
    CHECK(memcmp(cmd.args + 3, "\xc0\xa8\x04\x17\xc0\xa8\x04\x63", 8) == 0);
    c.boot_id = 1;  /* the board rebooted: an old command is not accepted */
    CHECK(wsn_parse_cmd(&c, buf, n, &seq, &cmd) == -6);
    c.boot_id = 0xDEADBEEF;
    CHECK(wsn_parse_hub(&c, buf, n, &seq, &hub) == -4);

    n = unhex(hex[2], buf);
    CHECK(wsn_parse_cmd(&c, buf, n, &seq, &cmd) == 0);
    CHECK(seq == 10 && cmd.cmd == WSN_CMD_LINKS && cmd.len == 1 + 10 && cmd.args[0] == 1);
    CHECK(memcmp(cmd.args + 1, "\xd8\xd6\x68\xb3\x4c\x23\xc0\xa8\x04\x02", 10) == 0);
    printf("OK hub and command\n");
    return 0;
}
