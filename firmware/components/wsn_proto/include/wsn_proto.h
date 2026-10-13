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
#define WSN_TYPE_MODEL_CHUNK 6 /* server -> board: piece of a new model blob */
#define WSN_TYPE_MODEL_ACK 7   /* board -> server: how much of it is stored */
#define WSN_MODEL_CHUNK_HDR 14 /* version u32, total u32, offset u32, len u16 */
#define WSN_MODEL_CHUNK_MAX 1024
#define WSN_MODEL_ACK_LEN 11   /* version u32, received u32, state u8, slot u8, error u8 */
#define WSN_TYPE_DIAG 8        /* board -> server: why the previous boots ended */
#define WSN_DIAG_HIST 16
#define WSN_DIAG_MSG 48        /* incl. NUL */
#define WSN_DIAG_BT 8          /* backtrace depth of the last panic */

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

typedef struct {
    uint32_t version, total, offset;
    uint16_t len;
    const uint8_t *data;  /* points into the received datagram */
} wsn_model_chunk_t;

typedef enum { WSN_OTA_IDLE = 0, WSN_OTA_RECEIVING = 1, WSN_OTA_ACTIVATING = 2, WSN_OTA_ERROR = 3 } wsn_ota_state_t;

typedef struct {
    uint32_t version;   /* blob being received (or last one) */
    uint32_t received;  /* bytes stored without gaps from offset 0 */
    uint8_t state;      /* wsn_ota_state_t */
    uint8_t slot;       /* partition being written: 0 = model_a, 1 = model_b */
    uint8_t error;      /* last error code, 0 = none */
} wsn_model_ack_t;

/* Body: boots u32, prev_uptime_s u32, panic_pc u32, panic_exc u8, panic_core u8, n u8, n x (reason u8, stage u8),
 * msg_len u8, msg (no NUL), then rom_rst[2] u8, early_kind u8, early_cause u8, early_pc u32, bt_n u8, bt_n x u32. */
typedef struct {
    uint32_t boots;          /* boots counted since the counter was created */
    uint32_t prev_uptime_s;  /* how long the previous boot ran (0 = unknown: power-on / brownout) */
    uint32_t panic_pc;       /* address of the last panic of the previous boot */
    uint8_t panic_exc;       /* 0 = previous boot did not panic, else panic_exception_t + 1 */
    uint8_t panic_core;
    uint8_t n;               /* history length, newest first */
    uint8_t reason[WSN_DIAG_HIST]; /* esp_reset_reason_t */
    uint8_t stage[WSN_DIAG_HIST];  /* boot stage reached; bit 0x80 = USB host present */
    char msg[WSN_DIAG_MSG];  /* panic reason / abort message, NUL-terminated */
    uint8_t rom_rst[2];      /* raw ROM reset reason of this boot per core (soc_reset_reason_t: which watchdog) */
    uint8_t early_kind;      /* previous boot entered the exception vector: 0 no, 1 interrupt (IWDT, cache error),
                                2 CPU exception; set even when the panic handler itself died */
    uint8_t early_cause;     /* EXCCAUSE (kind 2) or PANIC_RSN_* (kind 1) */
    uint32_t early_pc;
    uint8_t bt_n;            /* backtrace of the last panic (as printed by ESP-IDF: PC - 3 after the first) */
    uint32_t bt[WSN_DIAG_BT];
} wsn_diag_t;

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
/* Encode the OTA progress report (WSN_TYPE_MODEL_ACK). Same return convention. */
int wsn_encode_model_ack(wsn_ctx_t *ctx, const wsn_model_ack_t *a, uint8_t *out, size_t cap);
/* Encode the reboot history (WSN_TYPE_DIAG). Same return convention; -5 = n > WSN_DIAG_HIST or bt_n > WSN_DIAG_BT. */
int wsn_encode_diag(wsn_ctx_t *ctx, const wsn_diag_t *d, uint8_t *out, size_t cap);
/* Verify and parse a server packet carrying a model chunk. It must be addressed to ctx->device_id, signed with
 * ctx->key and carry ctx->boot_id (binds it to this boot: an old capture can not be replayed after a reboot).
 * *seq receives the server sequence number (caller rejects seq <= last). Returns 0 or a negative error. */
int wsn_parse_model_chunk(const wsn_ctx_t *ctx, const uint8_t *buf, size_t len, uint32_t *seq, wsn_model_chunk_t *out);

#ifdef __cplusplus
}
#endif
