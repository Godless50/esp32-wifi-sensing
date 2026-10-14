#include "uplink.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "csi_capture.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "guard.h"
#include "infer.h"
#include "memguard.h"
#include "model_ota.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "mbedtls/md.h"
#include "panic_trace.h"
#include "ping_gen.h"
#include "survey.h"
#include "wifi_mgr.h"
#include "wsn_proto.h"

static const char *TAG = "uplink";

#define FLUSH_US 50000
#define STALL_HZ 5.0f  /* ответов точки доступа почти нет: пересоздать сокет пинга */
#define STALL_SECONDS 10

#define RESOLVE_RETRY_US 5000000
#define DIAG_EVERY_US 60000000 /* история перезапусков: сервер мог перезапуститься и её не видеть */

void diag_get(wsn_diag_t *d); /* app_main.c */
#define GUARD_ARM_S 40          /* снимок указателей Wi-Fi в ПЗУ — когда инициализация точно закончилась */
#define STACKS_EVERY_US 300000000 /* отчёт о стеках задач: через минуту после старта и раз в 5 минут */

#define LOG_Q 8
static char s_logq[LOG_Q][WSN_LOG_MAX + 1];
static uint32_t s_log_w, s_log_r;
static portMUX_TYPE s_log_mux = portMUX_INITIALIZER_UNLOCKED;

void uplink_log(const char *fmt, ...)
{
    char line[WSN_LOG_MAX + 1];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    portENTER_CRITICAL(&s_log_mux);
    if (s_log_w - s_log_r < LOG_Q) {
        memcpy(s_logq[s_log_w % LOG_Q], line, sizeof(line));
        s_log_w++;
    }
    portEXIT_CRITICAL(&s_log_mux);
    ESP_LOGW("remote", "%s", line);
}

static bool log_pop(char *line)
{
    bool ok = false;
    portENTER_CRITICAL(&s_log_mux);
    if (s_log_r != s_log_w) {
        memcpy(line, s_logq[s_log_r % LOG_Q], WSN_LOG_MAX + 1);
        s_log_r++;
        ok = true;
    }
    portEXIT_CRITICAL(&s_log_mux);
    return ok;
}

static wsn_settings_t s_cfg;
static uint32_t s_boot_id;
static volatile float s_csi_hz;
static uint32_t s_sound_n; /* зондов сервера с прошлой телеметрии */
#define SOUND_ON_HZ 30     /* столько зондов в секунду — сервер «звучит», свой пинг почти не нужен */
#define PING_WITH_SOUND_HZ 5

static void hmac_sha256(const uint8_t *key, size_t key_len, const uint8_t *msg, size_t len, uint8_t out[32])
{
    mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, key_len, msg, len, out);
}

float uplink_csi_hz(void) { return s_csi_hz; }

#define LISTEN_PORT 5005 /* центр (телефон) рассылает свой адрес широковещательно на этот порт */

static int open_socket(struct sockaddr_in *dst)
{
    struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_DGRAM};
    struct addrinfo *res = NULL;
    if (getaddrinfo(s_cfg.host, NULL, &hints, &res) != 0 || !res) return -1;
    *dst = *(struct sockaddr_in *)res->ai_addr;
    dst->sin_port = htons(s_cfg.port);
    freeaddrinfo(res);
    survey_set_hub(dst->sin_addr.s_addr);
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) return sock;
    int one = 1;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &one, sizeof(one));
    struct sockaddr_in me = {.sin_family = AF_INET, .sin_port = htons(LISTEN_PORT), .sin_addr.s_addr = INADDR_ANY};
    if (bind(sock, (struct sockaddr *)&me, sizeof(me)) != 0)  /* без этого объявления центра не дойдут */
        ESP_LOGW(TAG, "bind :%d failed (errno %d): hub announcements will not reach this board", LISTEN_PORT, errno);
    return sock;
}

/* Hub announcement: from now on send to ip:port. Stored when the address changes (the board must find the hub
 * after a reboot); seq protects from replays of old announcements. */
static bool on_hub(const wsn_hub_t *h, uint32_t seq, struct sockaddr_in *dst)
{
    if (seq <= s_cfg.hub_seq) return false;
    s_cfg.hub_seq = seq;
    char host[16];
    snprintf(host, sizeof(host), "%u.%u.%u.%u", h->ip[0], h->ip[1], h->ip[2], h->ip[3]);
    if (strcmp(host, s_cfg.host) == 0 && h->port == s_cfg.port) return false;
    strlcpy(s_cfg.host, host, sizeof(s_cfg.host));
    s_cfg.port = h->port;
    memcpy(&dst->sin_addr.s_addr, h->ip, 4);
    dst->sin_port = htons(h->port);
    survey_set_hub(dst->sin_addr.s_addr);
    static wsn_settings_t st;
    csi_capture_flash_begin();
    esp_err_t err = settings_load(&st);
    if (err == ESP_OK) {
        strlcpy(st.host, host, sizeof(st.host));
        st.port = h->port;
        st.hub_seq = seq;
        err = settings_save(&st);
    }
    csi_capture_flash_end();
    ESP_LOGI(TAG, "hub moved: %s:%u (%s)", host, h->port, err == ESP_OK ? "stored" : esp_err_to_name(err));
    return true;
}

static void on_cmd(const wsn_cmd_t *c)
{
    if (c->cmd == WSN_CMD_SURVEY && c->len >= 3 && c->args[2] <= WSN_CMD_SURVEY_MAX_EXCL &&
        c->len == 3u + 4u * c->args[2]) {
        survey_args_t a = {.seconds = c->args[0], .max_links = c->args[1], .n_excl = c->args[2], .apply = true};
        memcpy(a.excl, c->args + 3, 4u * a.n_excl);
        esp_err_t err = survey_start(&a);
        if (err != ESP_OK) ESP_LOGW(TAG, "survey: %s", esp_err_to_name(err));
    } else if (c->cmd == WSN_CMD_LINKS && c->len >= 1 && c->args[0] <= WSN_MAX_DEVICE_LINKS &&
               c->len == 1u + 10u * c->args[0]) {
        uint8_t macs[WSN_MAX_DEVICE_LINKS][6];
        uint32_t ips[WSN_MAX_DEVICE_LINKS];
        for (int i = 0; i < c->args[0]; i++) {
            memcpy(macs[i], c->args + 1 + 10 * i, 6);
            memcpy(&ips[i], c->args + 7 + 10 * i, 4);
        }
        survey_set_links(macs, ips, c->args[0]);
    } else {
        ESP_LOGW(TAG, "unknown or malformed command %u (%u bytes)", c->cmd, c->len);
    }
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
    int64_t next_stacks = 60000000;
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
        if (sock >= 0) {  /* от сервера: куски новой модели (подписаны, привязаны к этой загрузке) и «зонд» */
            static uint8_t rx[WSN_MAX_DATAGRAM];
            static uint32_t last_seq, last_cmd_seq;
            /* Сервер шлёт каждой плате ~100 крошечных датаграмм «WSNS» в секунду: их передаёт плате её точка
             * доступа, и каждая — замер CSI линии «точка доступа -> плата». Читаем всё, что накопилось, иначе
             * при одном чтении за проход (~10 мс) куски модели ждали бы в очереди за зондами. */
            for (int k = 0; k < 16; k++) {
                int r = recvfrom(sock, rx, sizeof(rx), MSG_DONTWAIT, NULL, NULL);
                if (r <= 0) break;
                if (r >= 4 && memcmp(rx, "WSNS", 4) == 0) {
                    s_sound_n++;
                    continue;
                }
                wsn_model_chunk_t ch;
                uint32_t seq;
                if (r < WSN_HEADER_LEN) continue;
                if (rx[5] == WSN_TYPE_MODEL_CHUNK) {
                    if (wsn_parse_model_chunk(&ctx, rx, (size_t)r, &seq, &ch) == 0 && seq > last_seq) {
                        last_seq = seq;
                        model_ota_feed(&ch);
                    }
                } else if (rx[5] == WSN_TYPE_HUB) {
                    wsn_hub_t hub;
                    if (wsn_parse_hub(&ctx, rx, (size_t)r, &seq, &hub) == 0 && on_hub(&hub, seq, &dst))
                        next_diag = 0;  /* новый центр: история перезапусков сразу */
                } else if (rx[5] == WSN_TYPE_CMD) {
                    wsn_cmd_t cmd;
                    if (wsn_parse_cmd(&ctx, rx, (size_t)r, &seq, &cmd) == 0 && seq > last_cmd_seq) {
                        last_cmd_seq = seq;
                        on_cmd(&cmd);
                    }
                }
            }
        }
        wsn_model_ack_t ack;
        if (model_ota_status(&ack)) send_pkt(sock, &dst, out, wsn_encode_model_ack(&ctx, &ack, out, sizeof(out)));
        static wsn_survey_t sv;
        if (sock >= 0 && survey_report(&sv)) send_pkt(sock, &dst, out, wsn_encode_survey(&ctx, &sv, out, sizeof(out)));
        if (sock >= 0) {
            static char line[WSN_LOG_MAX + 1];
            if (log_pop(line)) send_pkt(sock, &dst, out, wsn_encode_log(&ctx, line, out, sizeof(out)));
        }
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
            /* Пинг шлюза — запасной источник кадров точки доступа: пока сервер присылает зонды, хватает 5 Гц
             * (иначе 3 платы x 100 Гц ICMP упирались в ограничение роутера: ответы одной платы пропадали). */
            ping_gen_set_hz(s_sound_n / dt >= SOUND_ON_HZ ? PING_WITH_SOUND_HZ : s_cfg.ping_hz);
            s_sound_n = 0;
            if (t.uptime_s >= GUARD_ARM_S) {
                guard_arm(); /* один раз за загрузку */
                guard_check();
            }
            if ((t.uptime_s & 1) == 0) guard_heap_check();
            if (now >= next_stacks) {
                guard_report_stacks();
                next_stacks = now + STACKS_EVERY_US;
            }
            if (sock >= 0 && now >= next_diag) {
                static wsn_diag_t d;
                diag_get(&d);
                send_pkt(sock, &dst, out, wsn_encode_diag(&ctx, &d, out, sizeof(out)));
                next_diag = now + DIAG_EVERY_US;
            }
            static uint32_t guarded;
            uint32_t caller, g = memguard_skipped(&caller);
            if (g != guarded) {  /* библиотека Wi-Fi пыталась скопировать кадр отрицательной длины: пропущено */
                uplink_log("memguard: %lu bad copies skipped (last from 0x%08lx)", (unsigned long)g, (unsigned long)caller);
                guarded = g;
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
