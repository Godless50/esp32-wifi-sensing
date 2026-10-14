/* Survey of the home network — see survey.h. */
#include "survey.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "csi_capture.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/inet_chksum.h"
#include "lwip/prot/icmp.h"
#include "lwip/sockets.h"
#include "ping_gen.h"
#include "wsn_util.h"

static const char *TAG = "survey";

#define ICMP_ID 0x5755      /* "WU": не путать с ответами постоянного пинга (0x5753) */
#define WAVE 8              /* запросов подряд: столько адресов сразу ждут ARP (в таблице lwIP всего 10 мест) */
#define BASE_SLOTS 4        /* окна без пинга: фон каждого передатчика */
#define SLOT_MACS 16        /* передатчиков на окно (самые частые) */
#define ATTR_HZ 25          /* пинг в окне устройства */
#define MAX_HOSTS 254       /* опрашиваем не больше /24 вокруг своего адреса */
#define MIN_EXCESS 3        /* кадров сверх фона, чтобы считать передатчик откликнувшимся */
#define MIN_YIELD 30        /* x100: кадров на запрос, чтобы стать линией (6 Гц при пинге 20 Гц) */
#define MIN_RSSI (-85)
#define RESEND_US 3000000
#define RESEND_FOR_US 30000000

typedef struct {
    uint8_t mac[6];
    uint16_t frames, ht;
    int8_t rssi;
} slot_mac_t;

typedef struct {
    uint32_t ip;  /* 0 = окно без пинга (фон) */
    uint16_t pings, replies;
    uint8_t n;
    slot_mac_t m[SLOT_MACS];
} slot_t;

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static wsn_survey_t s_rep;
static bool s_rep_changed;
static int64_t s_resend_until, s_resend_next;
static volatile bool s_running;
static volatile uint32_t s_hub_ip;
static survey_args_t s_args;
static csi_src_stat_t s_src[CSI_SRC_MAX];

void survey_set_hub(uint32_t ip) { s_hub_ip = ip; }
bool survey_running(void) { return s_running; }

static void set_progress(uint8_t state, uint8_t progress, uint8_t responders)
{
    portENTER_CRITICAL(&s_mux);
    bool ch = s_rep.state != state || s_rep.responders != responders || progress >= s_rep.progress + 5 ||
              progress < s_rep.progress;
    s_rep.state = state;
    s_rep.progress = progress;
    s_rep.responders = responders;
    if (state != WSN_SURVEY_DONE) s_rep.n = 0;
    if (ch) s_rep_changed = true;
    portEXIT_CRITICAL(&s_mux);
}

bool survey_report(wsn_survey_t *out)
{
    int64_t now = esp_timer_get_time();
    bool give = false;
    portENTER_CRITICAL(&s_mux);
    if (s_rep_changed) {
        give = true;
        s_rep_changed = false;
    } else if ((s_rep.state == WSN_SURVEY_DONE || s_rep.state == WSN_SURVEY_FAILED) && now < s_resend_until &&
               now >= s_resend_next) {
        give = true;
        s_resend_next = now + RESEND_US;
    }
    if (give) *out = s_rep;
    portEXIT_CRITICAL(&s_mux);
    return give;
}

static void send_echo(int sock, uint32_t addr, uint16_t seq)
{
    uint8_t pkt[sizeof(struct icmp_echo_hdr) + 8];
    struct icmp_echo_hdr *h = (struct icmp_echo_hdr *)pkt;
    memset(pkt, 0, sizeof(pkt));
    h->type = ICMP_ECHO;
    h->id = ICMP_ID;
    h->seqno = lwip_htons(seq);
    memcpy(pkt + sizeof(*h), "wsn-scan", 8);
    h->chksum = inet_chksum(pkt, sizeof(pkt));
    struct sockaddr_in to = {.sin_family = AF_INET, .sin_addr.s_addr = addr};
    sendto(sock, pkt, sizeof(pkt), 0, (struct sockaddr *)&to, sizeof(to));
}

/* Next echo reply to our requests (id ICMP_ID): source address, or 0 when none is waiting. */
static uint32_t next_reply(int sock)
{
    uint8_t rx[96];
    for (;;) {
        struct sockaddr_in from;
        socklen_t fl = sizeof(from);
        int r = recvfrom(sock, rx, sizeof(rx), MSG_DONTWAIT, (struct sockaddr *)&from, &fl);
        if (r <= 0) return 0;
        int ihl = (rx[0] & 0x0f) * 4;  /* сырой сокет отдаёт пакет с заголовком IP */
        if (r < ihl + (int)sizeof(struct icmp_echo_hdr)) continue;
        struct icmp_echo_hdr h;
        memcpy(&h, rx + ihl, sizeof(h));
        if (h.type == ICMP_ER && h.id == ICMP_ID) return from.sin_addr.s_addr;
    }
}

static bool excluded(uint32_t ip, uint32_t me)
{
    if (ip == me || ip == s_hub_ip) return true;
    for (int i = 0; i < s_args.n_excl; i++)
        if (s_args.excl[i] == ip) return true;
    return false;
}

static int add_responder(uint32_t *resp, int n, uint32_t ip)
{
    for (int i = 0; i < n; i++)
        if (resp[i] == ip) return n;
    if (n < WSN_SURVEY_MAX) resp[n++] = ip;
    return n;
}

/* Which hosts of the subnet answer an echo request: two passes, the second only over the silent ones and with
 * a longer wait (devices in power save answer late). Returns the number of responders. */
static int discover(int sock, uint32_t me, uint32_t mask, uint32_t *resp, int *total_responders)
{
    uint32_t me_h = lwip_ntohl(me), mask_h = lwip_ntohl(mask);
    if (mask_h < 0xFFFFFF00u) mask_h = 0xFFFFFF00u;
    uint32_t net_h = me_h & mask_h;
    int hosts = (int)(~mask_h) - 1;
    if (hosts < 1) return 0;
    if (hosts > MAX_HOSTS) hosts = MAX_HOSTS;
    static uint8_t answered[(MAX_HOSTS + 8) / 8];
    memset(answered, 0, sizeof(answered));
    int n = 0, total = 0;
    uint16_t seq = 0;
    for (int pass = 0; pass < 2; pass++) {
        for (int h = 1; h <= hosts; h += WAVE) {
            for (int k = h; k < h + WAVE && k <= hosts; k++) {
                uint32_t ip = lwip_htonl(net_h | (uint32_t)k);
                if ((answered[k / 8] >> (k % 8)) & 1 || excluded(ip, me)) continue;
                send_echo(sock, ip, ++seq);
                vTaskDelay(pdMS_TO_TICKS(3));
            }
            int64_t until = esp_timer_get_time() + (pass ? 250000 : 150000);
            while (esp_timer_get_time() < until) {
                uint32_t from;
                while ((from = next_reply(sock)) != 0) {
                    uint32_t k = lwip_ntohl(from) - net_h;
                    if ((lwip_ntohl(from) & mask_h) != net_h || k < 1 || k > (uint32_t)hosts || excluded(from, me))
                        continue;
                    if (!((answered[k / 8] >> (k % 8)) & 1)) {
                        answered[k / 8] |= (uint8_t)(1 << (k % 8));
                        total++;
                        n = add_responder(resp, n, from);
                    }
                }
                vTaskDelay(pdMS_TO_TICKS(3));
            }
            int done = pass * hosts + h + WAVE;
            set_progress(WSN_SURVEY_DISCOVER, (uint8_t)(done * 25 / (2 * hosts) > 25 ? 25 : done * 25 / (2 * hosts)),
                         (uint8_t)(total > 255 ? 255 : total));
        }
    }
    *total_responders = total;
    return n;
}

static int cmp_frames(const void *a, const void *b)
{
    return (int)((const csi_src_stat_t *)b)->frames - (int)((const csi_src_stat_t *)a)->frames;
}

/* One window: ping `ip` (0 = nobody) at ATTR_HZ for `us`, then keep the transmitters heard most. */
static void run_slot(int sock, slot_t *sl, uint32_t ip, int64_t us)
{
    csi_capture_sources(NULL, 0, true);
    memset(sl, 0, sizeof(*sl));
    sl->ip = ip;
    int64_t t0 = esp_timer_get_time(), next = t0;
    uint16_t seq = 0;
    while (esp_timer_get_time() - t0 < us) {
        if (ip && esp_timer_get_time() >= next) {
            send_echo(sock, ip, ++seq);
            sl->pings++;
            next += 1000000 / ATTR_HZ;
        }
        uint32_t from;
        while ((from = next_reply(sock)) != 0)
            if (from == ip) sl->replies++;
        vTaskDelay(pdMS_TO_TICKS(4));
    }
    size_t n = csi_capture_sources(s_src, CSI_SRC_MAX, true);
    qsort(s_src, n, sizeof(s_src[0]), cmp_frames);
    for (size_t i = 0; i < n && i < SLOT_MACS; i++) {
        slot_mac_t *m = &sl->m[sl->n++];
        memcpy(m->mac, s_src[i].mac, 6);
        m->frames = s_src[i].frames > 65535 ? 65535 : (uint16_t)s_src[i].frames;
        m->ht = s_src[i].ht > 65535 ? 65535 : (uint16_t)s_src[i].ht;
        m->rssi = s_src[i].frames ? (int8_t)(s_src[i].rssi_sum / (int32_t)s_src[i].frames) : 0;
    }
}

static int frames_in(const slot_t *sl, const uint8_t mac[6])
{
    for (int i = 0; i < sl->n; i++)
        if (memcmp(sl->m[i].mac, mac, 6) == 0) return sl->m[i].frames;
    return 0;
}

/* Background of a transmitter: median over the windows without ping. */
static int background(const slot_t *base, int nb, const uint8_t mac[6])
{
    int v[BASE_SLOTS];
    for (int i = 0; i < nb; i++) v[i] = frames_in(&base[i], mac);
    for (int i = 1; i < nb; i++)
        for (int j = i; j > 0 && v[j - 1] > v[j]; j--) {
            int t = v[j];
            v[j] = v[j - 1];
            v[j - 1] = t;
        }
    return nb ? (v[(nb - 1) / 2] + v[nb / 2]) / 2 : 0;
}

typedef struct {
    uint8_t mac[6];
    uint8_t slots; /* в скольких окнах устройств этот передатчик откликается */
} cand_t;

static bool is_candidate(const slot_t *sl, const slot_mac_t *m, const slot_t *base, int nb, int *excess)
{
    if (csi_capture_link_kind(m->mac) == CSI_LINK_AP) return false;  /* пересылка точкой доступа — не новая линия */
    *excess = m->frames - background(base, nb, m->mac);
    int need = sl->pings * 3 / 10;
    return *excess >= (need > MIN_EXCESS ? need : MIN_EXCESS);
}

static int score(const wsn_survey_entry_t *e)
{
    int y = e->yield > 100 ? 100 : e->yield;
    return y * 4 + (e->rssi + 100) + e->ht / 10;
}

static void analyse(slot_t *slots, int ns, slot_t *base, int nb, int responders, wsn_survey_t *rep)
{
    static cand_t cand[64];
    int nc = 0;
    for (int i = 0; i < ns; i++) {  /* передатчик, откликающийся в окнах многих устройств, — узел mesh-ретранслятор */
        for (int j = 0; j < slots[i].n; j++) {
            int ex;
            if (!is_candidate(&slots[i], &slots[i].m[j], base, nb, &ex)) continue;
            int c;
            for (c = 0; c < nc && memcmp(cand[c].mac, slots[i].m[j].mac, 6) != 0; c++) {
            }
            if (c == nc) {
                if (nc == 64) continue;
                memcpy(cand[nc].mac, slots[i].m[j].mac, 6);
                cand[nc++].slots = 0;
            }
            cand[c].slots++;
        }
    }
    memset(rep, 0, sizeof(*rep));
    rep->state = WSN_SURVEY_DONE;
    rep->progress = 100;
    rep->responders = (uint8_t)(responders > 255 ? 255 : responders);
    for (int i = 0; i < ns && rep->n < WSN_SURVEY_MAX; i++) {
        const slot_t *sl = &slots[i];
        wsn_survey_entry_t *e = &rep->e[rep->n++];
        memcpy(e->ip, &sl->ip, 4);  /* порядок lwIP = порядок байт адреса */
        int best = -1, best_slots = 1 << 30, best_ex = 0;
        for (int j = 0; j < sl->n; j++) {
            int ex;
            if (!is_candidate(sl, &sl->m[j], base, nb, &ex)) continue;
            int c;
            for (c = 0; c < nc && memcmp(cand[c].mac, sl->m[j].mac, 6) != 0; c++) {
            }
            int cs = c < nc ? cand[c].slots : 1;
            if (cs < best_slots || (cs == best_slots && ex > best_ex)) {
                best = j;
                best_slots = cs;
                best_ex = ex;
            }
        }
        if (best < 0 || !sl->pings) {
            e->flags = WSN_SURVEY_F_UNHEARD;
            continue;
        }
        const slot_mac_t *m = &sl->m[best];
        memcpy(e->mac, m->mac, 6);
        int y = best_ex * 100 / sl->pings;
        e->yield = (uint8_t)(y > 255 ? 255 : y);
        e->rssi = m->rssi;
        e->ht = (uint8_t)(m->frames ? m->ht * 100 / m->frames : 0);
        int kind = csi_capture_link_kind(m->mac);
        if (kind == CSI_LINK_BOARD) e->flags |= WSN_SURVEY_F_KNOWN;
        if (m->mac[0] & 0x02) e->flags |= WSN_SURVEY_F_RANDOM_MAC;
    }
    /* выбор: слышно хорошо, не телефон, не уже известная линия; по одному на передатчик */
    for (int k = 0; k < s_args.max_links; k++) {
        int pick = -1;
        for (int i = 0; i < rep->n; i++) {
            wsn_survey_entry_t *e = &rep->e[i];
            if (e->flags & (WSN_SURVEY_F_CHOSEN | WSN_SURVEY_F_UNHEARD | WSN_SURVEY_F_KNOWN | WSN_SURVEY_F_RANDOM_MAC))
                continue;
            if (e->yield < MIN_YIELD || e->rssi < MIN_RSSI) continue;
            bool dup = false;
            for (int j = 0; j < rep->n && !dup; j++)
                dup = (rep->e[j].flags & WSN_SURVEY_F_CHOSEN) && memcmp(rep->e[j].mac, e->mac, 6) == 0;
            if (dup) continue;
            if (pick < 0 || score(e) > score(&rep->e[pick])) pick = i;
        }
        if (pick < 0) break;
        rep->e[pick].flags |= WSN_SURVEY_F_CHOSEN;
    }
}

static void finish(const wsn_survey_t *rep)
{
    portENTER_CRITICAL(&s_mux);
    s_rep = *rep;
    s_rep_changed = true;
    s_resend_until = esp_timer_get_time() + RESEND_FOR_US;
    s_resend_next = esp_timer_get_time() + RESEND_US;
    portEXIT_CRITICAL(&s_mux);
}

static void fail(const char *why)
{
    ESP_LOGE(TAG, "failed: %s", why);
    static wsn_survey_t r;
    memset(&r, 0, sizeof(r));
    r.state = WSN_SURVEY_FAILED;
    finish(&r);
}

static void survey_task(void *arg)
{
    bool promisc_before = csi_capture_promisc();
    esp_netif_ip_info_t ii = {0};
    esp_netif_t *nif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    int sock = -1;
    slot_t *slots = NULL;
    if (!nif || esp_netif_get_ip_info(nif, &ii) != ESP_OK || !ii.ip.addr) {
        fail("no IP");
        goto out;
    }
    sock = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
    if (sock < 0) {
        fail("raw socket");
        goto out;
    }
    if (!promisc_before && csi_capture_set_promisc(true) != ESP_OK) {
        fail("sniffer");
        goto out;
    }
    int64_t t_start = esp_timer_get_time();
    static uint32_t resp[WSN_SURVEY_MAX];
    int total = 0;
    int n = discover(sock, ii.ip.addr, ii.netmask.addr, resp, &total);
    ESP_LOGI(TAG, "%d hosts answer (%d kept)", total, n);
    int ns = n + BASE_SLOTS;
    slots = heap_caps_calloc(ns, sizeof(slot_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!slots) slots = heap_caps_calloc(ns, sizeof(slot_t), MALLOC_CAP_8BIT);
    if (!slots) {
        fail("memory");
        goto out;
    }
    int64_t left = (int64_t)s_args.seconds * 1000000 - (esp_timer_get_time() - t_start) - 1000000;
    int64_t slot_us = left / ns;
    if (slot_us < 800000) slot_us = 800000;
    if (slot_us > 2000000) slot_us = 2000000;
    /* окна фона — в начале, на трети, на двух третях и в конце: фон эфира меняется */
    slot_t *base = slots + n;
    int nb = 0, d = 0;
    for (int i = 0; i < ns; i++) {
        bool is_base = nb < BASE_SLOTS && i == (nb * (ns - 1) + (BASE_SLOTS - 1) / 2) / (BASE_SLOTS - 1);
        if (is_base || d >= n) {
            if (nb < BASE_SLOTS) run_slot(sock, &base[nb++], 0, slot_us);
        } else {
            run_slot(sock, &slots[d], resp[d], slot_us);
            d++;
        }
        set_progress(WSN_SURVEY_ATTRIBUTE, (uint8_t)(25 + 70 * (i + 1) / ns), (uint8_t)(total > 255 ? 255 : total));
    }
    static wsn_survey_t rep;
    analyse(slots, n, base, nb, total, &rep);
    if (s_args.apply) {
        uint8_t macs[WSN_MAX_DEVICE_LINKS][6];
        uint32_t ips[WSN_MAX_DEVICE_LINKS];
        size_t k = 0;
        for (int i = 0; i < rep.n && k < WSN_MAX_DEVICE_LINKS; i++) {
            if (!(rep.e[i].flags & WSN_SURVEY_F_CHOSEN)) continue;
            memcpy(macs[k], rep.e[i].mac, 6);
            memcpy(&ips[k], rep.e[i].ip, 4);
            k++;
        }
        survey_set_links(macs, ips, k);
        promisc_before = k > 0;
    }
    finish(&rep);
    ESP_LOGI(TAG, "done in %lld s", (long long)((esp_timer_get_time() - t_start) / 1000000));
    survey_print();
out:
    if (!promisc_before && csi_capture_promisc()) csi_capture_set_promisc(false);
    if (sock >= 0) close(sock);
    free(slots);
    s_running = false;
    vTaskDelete(NULL);
}

esp_err_t survey_start(const survey_args_t *a)
{
    if (s_running) return ESP_ERR_INVALID_STATE;
    s_args = *a;
    if (s_args.seconds < 20) s_args.seconds = 20;
    if (s_args.max_links > WSN_MAX_DEVICE_LINKS) s_args.max_links = WSN_MAX_DEVICE_LINKS;
    if (s_args.n_excl > WSN_CMD_SURVEY_MAX_EXCL) s_args.n_excl = WSN_CMD_SURVEY_MAX_EXCL;
    s_running = true;
    set_progress(WSN_SURVEY_DISCOVER, 0, 0);
    if (xTaskCreatePinnedToCore(survey_task, "survey", 6144, NULL, 4, NULL, 1) != pdPASS) {
        s_running = false;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "started: %u s, up to %u links", s_args.seconds, s_args.max_links);
    return ESP_OK;
}

esp_err_t survey_set_links(const uint8_t (*macs)[6], const uint32_t *ips, size_t n)
{
    if (n > WSN_MAX_DEVICE_LINKS) n = WSN_MAX_DEVICE_LINKS;
    static wsn_settings_t st;
    csi_capture_flash_begin();
    esp_err_t err = settings_load(&st);
    if (err == ESP_OK) {
        st.n_links = (uint8_t)n;
        memset(st.links, 0, sizeof(st.links));
        memset(st.link_ip, 0, sizeof(st.link_ip));
        for (size_t i = 0; i < n; i++) {
            memcpy(st.links[i], macs[i], 6);
            st.link_ip[i] = ips[i];
        }
        st.promisc = n > 0;
        err = settings_save(&st);
    }
    /* запись кончилась: прослушивание — только если есть устройства-линии (продолжение захвата его не включит) */
    csi_capture_set_device_links(macs, n);
    if (csi_capture_promisc() != (n > 0)) csi_capture_set_promisc(n > 0);
    csi_capture_flash_end();
    ping_gen_set_targets(ips, n, err == ESP_OK ? st.tgt_hz : 20);
    if (err != ESP_OK) ESP_LOGE(TAG, "links not stored: %s", esp_err_to_name(err));
    ESP_LOGI(TAG, "%u device links", (unsigned)n);
    return err;
}

void survey_print(void)
{
    static wsn_survey_t r;
    portENTER_CRITICAL(&s_mux);
    r = s_rep;
    portEXIT_CRITICAL(&s_mux);
    for (int i = 0; i < r.n; i++) {
        const wsn_survey_entry_t *e = &r.e[i];
        char m[18];
        wsn_format_mac(e->mac, m);
        printf("SURVEY %u.%u.%u.%u %s per_ping=%u.%02u rssi=%d ht=%u%%%s%s%s%s\n", e->ip[0], e->ip[1], e->ip[2],
               e->ip[3], m, e->yield / 100, e->yield % 100, e->rssi, e->ht,
               e->flags & WSN_SURVEY_F_CHOSEN ? " chosen" : "", e->flags & WSN_SURVEY_F_KNOWN ? " known" : "",
               e->flags & WSN_SURVEY_F_RANDOM_MAC ? " random_mac" : "", e->flags & WSN_SURVEY_F_UNHEARD ? " unheard" : "");
    }
    printf("SURVEY_END state=%u responders=%u n=%u\n", r.state, r.responders, r.n);
}
