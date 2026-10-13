// Emits hex-encoded golden packets for server/tests/test_c_golden.py (one packet per line).
#include <stdio.h>
#include <string.h>

#include "hmac_openssl.h"
#include "wsn_proto.h"

static void hex(const uint8_t *b, int n)
{
    for (int i = 0; i < n; i++) printf("%02x", b[i]);
    putchar('\n');
}

int main(void)
{
    uint8_t key[32];
    for (int i = 0; i < 32; i++) key[i] = (uint8_t)i;
    wsn_ctx_t c = {.device_id = {0x02, 0x00, 0x00, 0x00, 0x00, 0x01}, .boot_id = 0xDEADBEEF, .seq = 7,
                   .key = key, .key_len = sizeof(key), .hmac = wsn_hmac_openssl};

    static int8_t data[256];
    for (int j = 0; j < 256; j++) data[j] = (int8_t)j;
    wsn_csi_frame_t f[5];
    for (int i = 0; i < 5; i++) {
        f[i] = (wsn_csi_frame_t){.t_us = 1000000 + i, .rssi = -45, .noise = -92, .sig_mode = 1,
                                 .cwb = 0, .len = 256, .data = data};
    }
    uint8_t out[WSN_MAX_DATAGRAM];
    int n = wsn_encode_csi(&c, f, 5, out, sizeof(out));
    if (n < 0) return 1;
    hex(out, n);

    /* seq is now 8: telemetry; seq 9: CSI with transmitter MACs (appended as 3rd line below) */
    wsn_telem_t t = {.csi_hz = 98.5f, .drop_ratio = 0.02f, .rssi_avg = -44, .heap_free = 120000,
                     .psram_free = 8000000, .uptime_s = 3600, .fw_ver = 1, .model_ver = 0,
                     .wifi_state = 2};
    n = wsn_encode_telem(&c, &t, out, sizeof(out));
    if (n < 0) return 1;
    hex(out, n);

    /* seq is now 9 */
    for (int i = 0; i < 2; i++) {
        const uint8_t src[6] = {0x98, 0xb3, 0xef, 0x02, 0xda, (uint8_t)(0x28 + i)};
        memcpy(f[i].src, src, 6);
        f[i].sig_mode = (uint8_t)i;
        f[i].len = i ? 256 : 128;
    }
    n = wsn_encode_csi_src(&c, f, 2, out, sizeof(out));
    if (n < 0) return 1;
    hex(out, n);

    /* seq 10: on-board model answer */
    wsn_pred_t r = {.model_ver = 1791870150u, .infer_ms = 87, .links_live = 8,
                    .act = {10, 200, 40, 5}, .zone = {180, 50, 20, 5}, .move = 230};
    n = wsn_encode_pred(&c, &r, out, sizeof(out));
    if (n < 0) return 1;
    hex(out, n);

    /* seq 11: OTA progress */
    wsn_model_ack_t ack = {.version = 1791871129u, .received = 2048, .state = WSN_OTA_RECEIVING, .slot = 1, .error = 0};
    n = wsn_encode_model_ack(&c, &ack, out, sizeof(out));
    if (n < 0) return 1;
    hex(out, n);
    return 0;
}
