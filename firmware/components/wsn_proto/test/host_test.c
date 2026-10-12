// Host unit tests for wsn_proto. Build: make -C firmware/components/wsn_proto/test test
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "hmac_openssl.h"
#include "wsn_proto.h"

static int8_t g_data[384];

static void fill(wsn_csi_frame_t *f, size_t n, uint16_t len)
{
    for (size_t i = 0; i < n; i++) {
        f[i] = (wsn_csi_frame_t){.t_us = 1000000 + i, .rssi = -45, .noise = -92, .sig_mode = 1,
                                 .cwb = 0, .len = len, .data = g_data};
    }
}

static wsn_ctx_t make_ctx(void)
{
    static const uint8_t key[32] = {0};
    wsn_ctx_t c = {.device_id = {2, 0, 0, 0, 0, 1}, .boot_id = 1, .seq = 0,
                   .key = key, .key_len = sizeof(key), .hmac = wsn_hmac_openssl};
    return c;
}

static void test_frames_fit(void)
{
    wsn_csi_frame_t f[12];
    fill(f, 12, 256);
    assert(wsn_frames_fit(f, 10) == 5);
    fill(f, 12, 384);
    assert(wsn_frames_fit(f, 10) == 3);
    fill(f, 12, 128);
    assert(wsn_frames_fit(f, 10) == 9);
    fill(f, 12, 16);
    assert(wsn_frames_fit(f, 12) == 10); /* capped by WSN_MAX_FRAMES */
    assert(wsn_frames_fit(f, 0) == 0);
}

static void test_encode_len_and_seq(void)
{
    wsn_csi_frame_t f[5];
    fill(f, 5, 256);
    uint8_t out[WSN_MAX_DATAGRAM];
    wsn_ctx_t c = make_ctx();
    int r = wsn_encode_csi(&c, f, 5, out, sizeof(out));
    assert(r == 21 + 5 * 270 + 16 && r == 1387);
    assert(c.seq == 1);
    assert(memcmp(out, "WSN1", 4) == 0 && out[4] == 1 && out[5] == WSN_TYPE_CSI && out[20] == 5);
}

static void test_encode_small_cap_fails(void)
{
    wsn_csi_frame_t f[5];
    fill(f, 5, 256);
    uint8_t out[100];
    wsn_ctx_t c = make_ctx();
    assert(wsn_encode_csi(&c, f, 5, out, sizeof(out)) < 0);
    assert(c.seq == 0);
}

static void test_encode_too_many_fails(void)
{
    wsn_csi_frame_t f[6];
    fill(f, 6, 256);
    uint8_t out[4096];
    wsn_ctx_t c = make_ctx();
    assert(wsn_encode_csi(&c, f, 6, out, sizeof(out)) < 0); /* > 1400 bytes */
    assert(c.seq == 0);
}

static void test_telem_len(void)
{
    wsn_telem_t t = {.csi_hz = 98.5f, .wifi_state = 2};
    uint8_t out[128];
    wsn_ctx_t c = make_ctx();
    assert(wsn_encode_telem(&c, &t, out, sizeof(out)) == 21 + 30 + 16);
    assert(out[5] == WSN_TYPE_TELEM && out[20] == 1 && c.seq == 1);
}

int main(void)
{
    test_frames_fit();
    test_encode_len_and_seq();
    test_encode_small_cap_fails();
    test_encode_too_many_fails();
    test_telem_len();
    puts("OK");
    return 0;
}
