// WSN1 packet encoder (spec 4.3). Pure C, no ESP-IDF dependencies: host-testable.
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WSN_MAX_DATAGRAM 1400
#define WSN_MAX_FRAMES 10
#define WSN_HEADER_LEN 21
#define WSN_FRAME_HDR_LEN 14
#define WSN_FRAME_SRC_HDR_LEN 20 /* + 6-byte transmitter MAC */
#define WSN_TELEM_LEN 30
#define WSN_PRED_LEN 17 /* model_ver u32, infer_ms u16, links_live u8, rsv u8, act[4], zone[4], move: u8 = p*255 */
#define WSN_HMAC_LEN 16

#define WSN_TYPE_CSI 1
#define WSN_TYPE_PRED 2
#define WSN_TYPE_TELEM 3
#define WSN_TYPE_CSI_SRC 4 /* CSI frames tagged with transmitter MAC (multi-link) */

typedef void (*wsn_hmac_fn)(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t len,
                            uint8_t out[32]);

typedef struct {
    uint8_t device_id[6];
    uint32_t boot_id;
    uint32_t seq;
    const uint8_t *key;
    size_t key_len;
    wsn_hmac_fn hmac;
} wsn_ctx_t;

typedef struct {
    uint64_t t_us;
    int8_t rssi, noise;
    uint8_t sig_mode, cwb;
    uint16_t len;
    const int8_t *data;
    uint8_t src[6]; /* transmitter MAC; encoded only in WSN_TYPE_CSI_SRC */
} wsn_csi_frame_t;

typedef struct {
    float csi_hz, drop_ratio;
    int8_t rssi_avg;
    uint32_t heap_free, psram_free, uptime_s, fw_ver, model_ver;
    uint8_t wifi_state;
} wsn_telem_t;

typedef struct {
    uint32_t model_ver;
    uint16_t infer_ms;   /* время признаков + сети на плате */
    uint8_t links_live;  /* сколько линий модели было живо в окне */
    uint8_t act[4];      /* вероятности x255: нет движения, машет, ходит, мелкие */
    uint8_t zone[4];     /* зал, кухня, коридор, санузел */
    uint8_t move;        /* кто-то движется */
} wsn_pred_t;

/* Number of leading frames that fit into one datagram (<= WSN_MAX_DATAGRAM, <= WSN_MAX_FRAMES). */
size_t wsn_frames_fit(const wsn_csi_frame_t *frames, size_t n);

/* Encode CSI packet. Returns length (>0) or negative error. On success ctx->seq is incremented. */
int wsn_encode_csi(wsn_ctx_t *ctx, const wsn_csi_frame_t *frames, size_t n, uint8_t *out, size_t cap);

/* Same for WSN_TYPE_CSI_SRC (frame header carries the transmitter MAC). */
size_t wsn_frames_fit_src(const wsn_csi_frame_t *frames, size_t n);
int wsn_encode_csi_src(wsn_ctx_t *ctx, const wsn_csi_frame_t *frames, size_t n, uint8_t *out, size_t cap);

/* Encode telemetry packet. Same return convention. */
int wsn_encode_telem(wsn_ctx_t *ctx, const wsn_telem_t *t, uint8_t *out, size_t cap);
/* Encode the on-board model's answer (WSN_TYPE_PRED). Same return convention. */
int wsn_encode_pred(wsn_ctx_t *ctx, const wsn_pred_t *r, uint8_t *out, size_t cap);

#ifdef __cplusplus
}
#endif
