#include "uplink.h"

#include <string.h>

#include "csi_capture.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "infer.h"
#include "model_ota.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "mbedtls/md.h"
#include "panic_trace.h"
#include "ping_gen.h"
#include "wifi_mgr.h"
#include "wsn_proto.h"

static const char *TAG = "uplink";

#define FLUSH_US 50000
#define STALL_HZ 20.0f
#define STALL_SECONDS 10

#define RESOLVE_RETRY_US 5000000
#define DIAG_EVERY_US 60000000 /* история перезапусков: сервер мог перезапуститься и её не видеть */

void diag_get(wsn_diag_t *d); /* app_main.c */

static wsn_settings_t s_cfg;
static uint32_t s_boot_id;
static volatile float s_csi_hz;

static void hmac_sha256(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t len, uint8_t out[32])
{
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, key_len, msg, len, out);
}

float uplink_csi_hz(void) { return s_csi_hz; }

static int open_socket(struct sockaddr_in *dst)
{
    struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_DGRAM};
    struct addrinfo *res = NULL;
    if (getaddrinfo(s_cfg.host, NULL, &hints, &res) != 0 || !res) return -1;
    *dst = *(struct sockaddr_in *)res->ai_addr;
    dst->sin_port = htons(s_cfg.port);
    freeaddrinfo(res);
    return socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
}

static void send_pkt(int sock, const struct sockaddr_in *dst, const uint8_t *buf, int len)
{
    if (sock < 0 || len <= 0) return;
    sendto(sock, buf, len, 0, (const struct sockaddr *)dst, sizeof(*dst)); /* best effort */
}

static void uplink_task(void *arg)
{
    static int8_t data[WSN_MAX_FRAMES + 1][CSI_MAX_LEN];
    static uint8_t out[WSN_MAX_DATAGRAM];
    wsn_csi_frame_t batch[WSN_MAX_FRAMES + 1];
    size_t n = 0;
    int64_t first_us = 0;

    wsn_ctx_t ctx = {.boot_id = s_boot_id, .seq = 0, .key = s_cfg.key, .key_len = 32, .hmac = hmac_sha256};
    esp_read_mac(ctx.device_id, ESP_MAC_WIFI_STA);

    int sock = -1;
    struct sockaddr_in dst = {0};
    csi_counters_t prev = {0};
    int64_t last_telem = esp_timer_get_time();
    int stall_s = 0;
    int64_t next_resolve = 0;
    int64_t next_diag = 0;
    esp_task_wdt_add(NULL);
    ESP_LOGI(TAG, "boot_id %lu -> %s:%u", (unsigned long)ctx.boot_id, s_cfg.host, s_cfg.port);

    for (;;) {
        esp_task_wdt_reset();
        if (sock < 0 && wifi_mgr_state() == WIFI_ST_CONNECTED && esp_timer_get_time() >= next_resolve) {
            sock = open_socket(&dst);
            if (sock < 0) { /* DNS/host problem: retry with a pause, never spin */
                ESP_LOGW(TAG, "cannot resolve/open %s, retry in 5 s", s_cfg.host);
                next_resolve = esp_timer_get_time() + RESOLVE_RETRY_US;
            }
        }

        int8_t *slot = data[n];
        if (csi_capture_pop(&batch[n], slot, pdMS_TO_TICKS(10))) {
            infer_push(&batch[n]);  /* копия амплитуды для модели на плате */
            if (n == 0) first_us = esp_timer_get_time();
            n++;
            if (wsn_frames_fit_src(batch, n) < n) { /* newest frame does not fit: flush the rest */
                send_pkt(sock, &dst, out, wsn_encode_csi_src(&ctx, batch, n - 1, out, sizeof(out)));
                memcpy(data[0], batch[n - 1].data, batch[n - 1].len);
                batch[0] = batch[n - 1];
                batch[0].data = data[0];
                n = 1;
                first_us = esp_timer_get_time();
            }
        }
        wsn_pred_t pred;
        if (infer_take(&pred)) send_pkt(sock, &dst, out, wsn_encode_pred(&ctx, &pred, out, sizeof(out)));
        if (sock >= 0) {  /* куски новой модели от сервера (подписаны, привязаны к этой загрузке) */
            static uint8_t rx[WSN_MAX_DATAGRAM];
            static uint32_t last_seq;
            int r = recvfrom(sock, rx, sizeof(rx), MSG_DONTWAIT, NULL, NULL);
            wsn_model_chunk_t ch;
            uint32_t seq;
            if (r > 0 && wsn_parse_model_chunk(&ctx, rx, (size_t)r, &seq, &ch) == 0 && seq > last_seq) {
                last_seq = seq;
                model_ota_feed(&ch);
            }
        }
        wsn_model_ack_t ack;
        if (model_ota_status(&ack)) send_pkt(sock, &dst, out, wsn_encode_model_ack(&ctx, &ack, out, sizeof(out)));
        int64_t now = esp_timer_get_time();
        if (n > 0 && (n >= WSN_MAX_FRAMES || now - first_us >= FLUSH_US)) {
            send_pkt(sock, &dst, out, wsn_encode_csi_src(&ctx, batch, n, out, sizeof(out)));
            n = 0;
        }

        if (now - last_telem >= 1000000) {
            csi_counters_t c;
            csi_capture_counters(&c);
            float dt = (now - last_telem) / 1e6f;
            uint32_t acc = c.accepted - prev.accepted, drop = c.dropped - prev.dropped;
            uint32_t rn = c.rssi_n - prev.rssi_n;
            s_csi_hz = acc / dt;
            wsn_telem_t t = {
                .csi_hz = s_csi_hz,
                .drop_ratio = (acc + drop) ? (float)drop / (acc + drop) : 0.0f,
                .rssi_avg = rn ? (int8_t)((c.rssi_sum - prev.rssi_sum) / (int64_t)rn) : 0,
                .heap_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                .psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
                .uptime_s = (uint32_t)(now / 1000000),
                .fw_ver = wsn_fw_id(),
                .model_ver = infer_model_ver(),
                .wifi_state = (uint8_t)wifi_mgr_state(),
            };
            send_pkt(sock, &dst, out, wsn_encode_telem(&ctx, &t, out, sizeof(out)));
            panic_trace_tick(t.uptime_s);
            if (sock >= 0 && now >= next_diag) {
                static wsn_diag_t d;
                diag_get(&d);
                send_pkt(sock, &dst, out, wsn_encode_diag(&ctx, &d, out, sizeof(out)));
                next_diag = now + DIAG_EVERY_US;
            }
            if (c.overflow != prev.overflow)
                ESP_LOGW(TAG, "ring overflow: %lu frames lost", (unsigned long)(c.overflow - prev.overflow));
            prev = c;
            last_telem = now;

            esp_ip4_addr_t gw;
            if (wifi_mgr_state() == WIFI_ST_CONNECTED && s_csi_hz < STALL_HZ && wifi_mgr_gateway(&gw)) {
                if (++stall_s >= STALL_SECONDS) {
                    ESP_LOGW(TAG, "csi stalled (%.1f Hz), restarting ping", s_csi_hz);
                    ping_gen_start(gw, s_cfg.ping_hz);
                    stall_s = 0;
                }
            } else {
                stall_s = 0;
            }
            if (wifi_mgr_state() != WIFI_ST_CONNECTED && sock >= 0) { /* re-resolve after reconnect */
                close(sock);
                sock = -1;
                next_diag = 0;
            }
        }
    }
}

esp_err_t uplink_start(const wsn_settings_t *s, uint32_t boot_id)
{
    s_cfg = *s;
    s_boot_id = boot_id;
    return xTaskCreatePinnedToCore(uplink_task, "uplink", 6144, NULL, 5, NULL, 1) == pdPASS ? ESP_OK
                                                                                              : ESP_ERR_NO_MEM;
}
