/* Parses server/tests/golden/py_model_chunk.hex (made by wsn.protocol.encode_model_chunk) with the C parser. */
#include <stdio.h>
#include <string.h>

#include "hmac_openssl.h"
#include "wsn_proto.h"

#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)

int main(int argc, char **argv)
{
    FILE *f = fopen(argc > 1 ? argv[1] : "../../../../server/tests/golden/py_model_chunk.hex", "r");
    CHECK(f);
    static char hex[4096];
    CHECK(fgets(hex, sizeof(hex), f));
    fclose(f);
    uint8_t buf[2048];
    size_t n = 0;
    for (char *p = hex; p[0] && p[1] && p[0] != '\n'; p += 2) { unsigned v; sscanf(p, "%2x", &v); buf[n++] = (uint8_t)v; }
    uint8_t key[32];
    for (int i = 0; i < 32; i++) key[i] = (uint8_t)i;
    wsn_ctx_t c = {.device_id = {0x02, 0, 0, 0, 0, 0x01}, .boot_id = 0xDEADBEEF, .key = key, .key_len = 32,
                   .hmac = wsn_hmac_openssl};
    uint32_t seq = 0;
    wsn_model_chunk_t ch;
    CHECK(wsn_parse_model_chunk(&c, buf, n, &seq, &ch) == 0);
    CHECK(seq == 5 && ch.version == 1791871129u && ch.total == 3000 && ch.offset == 1024 && ch.len == 100);
    for (int i = 0; i < 100; i++) CHECK(ch.data[i] == (uint8_t)i);
    c.boot_id = 1;  /* другой запуск платы: отклоняем */
    CHECK(wsn_parse_model_chunk(&c, buf, n, &seq, &ch) == -6);
    c.boot_id = 0xDEADBEEF;
    buf[30] ^= 1;   /* испорченные данные: подпись не сходится */
    CHECK(wsn_parse_model_chunk(&c, buf, n, &seq, &ch) == -3);
    buf[30] ^= 1;
    c.device_id[5] = 2;  /* чужая плата: подпись своим ключом сходится, но адресат не тот */
    CHECK(wsn_parse_model_chunk(&c, buf, n, &seq, &ch) == -5);
    printf("OK model chunk\n");
    return 0;
}
