#include "wsn_proto.h"

#include <stdbool.h>
#include <string.h>

static uint8_t *put_u8(uint8_t *p, uint8_t v) { *p++ = v; return p; }
static uint8_t *put_u16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; return p + 2; }
static uint8_t *put_u32(uint8_t *p, uint32_t v)
{
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i));
    return p + 4;
}
static uint8_t *put_u64(uint8_t *p, uint64_t v)
{
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i));
    return p + 8;
}
static uint8_t *put_f32(uint8_t *p, float f)
{
    uint32_t v;
    memcpy(&v, &f, 4);
    return put_u32(p, v);
}

static uint8_t *put_header(const wsn_ctx_t *c, uint8_t *p, uint8_t type, uint8_t n)
{
    memcpy(p, "WSN1", 4);
    p += 4;
    p = put_u8(p, 1);
    p = put_u8(p, type);
    memcpy(p, c->device_id, 6);
    p += 6;
    p = put_u32(p, c->boot_id);
    p = put_u32(p, c->seq);
    return put_u8(p, n);
}

static int finish(wsn_ctx_t *c, uint8_t *out, uint8_t *end)
{
    uint8_t mac[32];
    size_t body = (size_t)(end - out);
    c->hmac(c->key, c->key_len, out, body, mac);
    memcpy(end, mac, WSN_HMAC_LEN);
    c->seq++;
    return (int)(body + WSN_HMAC_LEN);
}

static size_t csi_size(const wsn_csi_frame_t *frames, size_t n, size_t hdr)
{
    size_t total = WSN_HEADER_LEN + WSN_HMAC_LEN;
    for (size_t i = 0; i < n; i++) total += hdr + frames[i].len;
    return total;
}

static size_t frames_fit(const wsn_csi_frame_t *frames, size_t n, size_t hdr)
{
    size_t total = WSN_HEADER_LEN + WSN_HMAC_LEN;
    size_t k = 0;
    while (k < n && k < WSN_MAX_FRAMES) {
        size_t next = total + hdr + frames[k].len;
        if (next > WSN_MAX_DATAGRAM) break;
        total = next;
        k++;
    }
    return k;
}

size_t wsn_frames_fit(const wsn_csi_frame_t *frames, size_t n) { return frames_fit(frames, n, WSN_FRAME_HDR_LEN); }

size_t wsn_frames_fit_src(const wsn_csi_frame_t *frames, size_t n)
{
    return frames_fit(frames, n, WSN_FRAME_SRC_HDR_LEN);
}

static int encode_csi(wsn_ctx_t *ctx, const wsn_csi_frame_t *frames, size_t n, uint8_t *out, size_t cap,
                      bool with_src)
{
    if (!ctx || !ctx->hmac || (!frames && n) || !out) return -1;
    if (n > WSN_MAX_FRAMES) return -2;
    size_t need = csi_size(frames, n, with_src ? WSN_FRAME_SRC_HDR_LEN : WSN_FRAME_HDR_LEN);
    if (need > WSN_MAX_DATAGRAM) return -3;
    if (need > cap) return -4;
    uint8_t *p = put_header(ctx, out, with_src ? WSN_TYPE_CSI_SRC : WSN_TYPE_CSI, (uint8_t)n);
    for (size_t i = 0; i < n; i++) {
        const wsn_csi_frame_t *f = &frames[i];
        p = put_u64(p, f->t_us);
        p = put_u8(p, (uint8_t)f->rssi);
        p = put_u8(p, (uint8_t)f->noise);
        p = put_u8(p, f->sig_mode);
        p = put_u8(p, f->cwb);
        p = put_u16(p, f->len);
        if (with_src) {
            memcpy(p, f->src, 6);
            p += 6;
        }
        memcpy(p, f->data, f->len);
        p += f->len;
    }
    return finish(ctx, out, p);
}

int wsn_encode_csi(wsn_ctx_t *ctx, const wsn_csi_frame_t *frames, size_t n, uint8_t *out, size_t cap)
{
    return encode_csi(ctx, frames, n, out, cap, false);
}

int wsn_encode_csi_src(wsn_ctx_t *ctx, const wsn_csi_frame_t *frames, size_t n, uint8_t *out, size_t cap)
{
    return encode_csi(ctx, frames, n, out, cap, true);
}

int wsn_encode_telem(wsn_ctx_t *ctx, const wsn_telem_t *t, uint8_t *out, size_t cap)
{
    if (!ctx || !ctx->hmac || !t || !out) return -1;
    if (cap < WSN_HEADER_LEN + WSN_TELEM_LEN + WSN_HMAC_LEN) return -4;
    uint8_t *p = put_header(ctx, out, WSN_TYPE_TELEM, 1);
    p = put_f32(p, t->csi_hz);
    p = put_f32(p, t->drop_ratio);
    p = put_u8(p, (uint8_t)t->rssi_avg);
    p = put_u32(p, t->heap_free);
    p = put_u32(p, t->psram_free);
    p = put_u32(p, t->uptime_s);
    p = put_u32(p, t->fw_ver);
    p = put_u32(p, t->model_ver);
    p = put_u8(p, t->wifi_state);
    return finish(ctx, out, p);
}

int wsn_encode_pred(wsn_ctx_t *ctx, const wsn_pred_t *r, uint8_t *out, size_t cap)
{
    if (!ctx || !ctx->hmac || !r || !out) return -1;
    if (cap < WSN_HEADER_LEN + WSN_PRED_LEN + WSN_HMAC_LEN) return -4;
    uint8_t *p = put_header(ctx, out, WSN_TYPE_PRED, 1);
    p = put_u32(p, r->model_ver);
    p = put_u16(p, r->infer_ms);
    p = put_u8(p, r->links_live);
    p = put_u8(p, 0);
    for (int i = 0; i < 4; i++) p = put_u8(p, r->act[i]);
    for (int i = 0; i < 4; i++) p = put_u8(p, r->zone[i]);
    p = put_u8(p, r->move);
    return finish(ctx, out, p);
}
