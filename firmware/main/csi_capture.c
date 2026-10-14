#include "csi_capture.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "settings.h"
#include "wsn_util.h"

static const char *TAG = "csi";

typedef struct __attribute__((packed)) {
    uint64_t t_us;
    int8_t rssi, noise;
    uint8_t sig_mode, cwb;
    uint16_t len;
    uint8_t src[6];
} item_hdr_t;

#define RING_BYTES (512 * 1024) /* ~10 s при ~180 кадрах/с: сеть Wi-Fi замирает на 6-9 с раз в минуту (13.10) */
#define RING_BYTES_INTERNAL (48 * 1024)

static RingbufHandle_t s_ring;
static uint8_t s_bssid[6];
static uint8_t s_source;
#define MAX_TX (1 + WSN_MAX_LINKS)
static uint8_t s_tx[MAX_TX][6]; /* [0] = точка доступа платы, дальше — дополнительные передатчики */
static uint8_t s_tx_kind[MAX_TX]; /* CSI_LINK_AP / CSI_LINK_BOARD / CSI_LINK_DEVICE */
static size_t s_tx_n;
static int64_t s_tx_last[MAX_TX];
#define LINK_MIN_US 15000 /* до ~66 Гц на передатчик: маяки ESP-NOW по 50 Гц проходят все (при 20000 из-за дрожания терялась половина) */
/* Точка доступа: до ~140 Гц. С пингом устройств дома к зондам центра (100 Гц) добавляются пересланные точкой
 * ответы устройств (6 x 20 Гц): без предела линия точки доступа одна занимала бы ~100 КБ/с отправки. */
#define AP_MIN_US 7000
static csi_counters_t s_cnt;
static bool s_promisc;  /* включено ли прослушивание (нужно для дополнительных линий) */
static SemaphoreHandle_t s_flash_lock; /* записи настроек из разных задач (опрос сети, адрес центра) */
static StaticSemaphore_t s_flash_buf;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

/* Диагностика: кто ещё на канале даёт CSI (видно в promiscuous). Не влияет на отправку. */
static csi_src_stat_t s_src[CSI_SRC_MAX];
static size_t s_src_n;

static void src_note(const uint8_t mac[6], const wifi_pkt_rx_ctrl_t *rx, uint16_t len)
{
    size_t i;
    for (i = 0; i < s_src_n && memcmp(s_src[i].mac, mac, 6) != 0; i++) {
    }
    if (i == s_src_n) {
        if (s_src_n == CSI_SRC_MAX) return;
        memset(&s_src[i], 0, sizeof(s_src[i]));
        memcpy(s_src[i].mac, mac, 6);
        s_src_n++;
    }
    s_src[i].frames++;
    if (rx->sig_mode == 1 && len >= 256) s_src[i].ht++;
    s_src[i].rssi_sum += rx->rssi;
}

size_t csi_capture_sources(csi_src_stat_t *out, size_t cap, bool reset)
{
    portENTER_CRITICAL(&s_mux);
    size_t n = out ? (s_src_n < cap ? s_src_n : cap) : 0;
    if (n) memcpy(out, s_src, n * sizeof(*out));
    if (reset) s_src_n = 0;
    portEXIT_CRITICAL(&s_mux);
    return n;
}

static void csi_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) return;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&s_mux);  /* список линий меняют другие задачи (опрос сети, ESP-NOW) */
    src_note(info->mac, &info->rx_ctrl, info->len);
    size_t k;
    for (k = 0; k < s_tx_n && memcmp(info->mac, s_tx[k], 6) != 0; k++) {
    }
    bool skip = k == s_tx_n || now - s_tx_last[k] < (k ? LINK_MIN_US : AP_MIN_US);
    if (!skip) s_tx_last[k] = now;
    portEXIT_CRITICAL(&s_mux);
    if (skip) return;
    uint16_t len = info->len > CSI_MAX_LEN ? CSI_MAX_LEN : info->len;
    const wifi_pkt_rx_ctrl_t *rx = &info->rx_ctrl;
    bool match = k == 0 && (s_source == 1 || (rx->sig_mode == 1 && len >= 256));

    item_hdr_t h = {.t_us = (uint64_t)now, .rssi = rx->rssi, .noise = rx->noise_floor,
                    .sig_mode = rx->sig_mode, .cwb = rx->cwb, .len = len};
    memcpy(h.src, info->mac, 6);
    void *slot = NULL;
    bool ok = xRingbufferSendAcquire(s_ring, &slot, sizeof(h) + len, 0) == pdTRUE;
    if (ok) {
        memcpy(slot, &h, sizeof(h));
        memcpy((uint8_t *)slot + sizeof(h), info->buf, len);
        xRingbufferSendComplete(s_ring, slot);
    }
    portENTER_CRITICAL(&s_mux);
    if (!ok) {
        s_cnt.overflow++;
    } else if (match) {
        s_cnt.accepted++;
    } else if (k == 0) {
        s_cnt.dropped++;
    } else {
        s_cnt.links++;
    }
    s_cnt.rssi_sum += rx->rssi;
    s_cnt.rssi_n++;
    portEXIT_CRITICAL(&s_mux);
}

bool csi_capture_add_link(const uint8_t mac[6])
{
    /* Вызывается из колбэка ESP-NOW (задача Wi-Fi, там же и csi_cb). Сначала адрес, потом счётчик:
     * csi_cb не увидит наполовину записанный элемент. */
    bool added = false;
    portENTER_CRITICAL(&s_mux);
    size_t k;
    for (k = 0; k < s_tx_n && memcmp(mac, s_tx[k], 6) != 0; k++) {
    }
    if (k == s_tx_n && s_tx_n > 0 && s_tx_n < MAX_TX) {
        memcpy(s_tx[s_tx_n], mac, 6);
        s_tx_kind[s_tx_n] = CSI_LINK_BOARD;
        s_tx_last[s_tx_n] = 0;
        __atomic_store_n(&s_tx_n, s_tx_n + 1, __ATOMIC_RELEASE);
        added = true;
    }
    portEXIT_CRITICAL(&s_mux);
    if (added) {
        char m[18];
        wsn_format_mac(mac, m);
        ESP_LOGI(TAG, "new board link %s (%u transmitters)", m, (unsigned)s_tx_n);
    }
    return added;
}

int csi_capture_link_kind(const uint8_t mac[6])
{
    int kind = -1;
    portENTER_CRITICAL(&s_mux);
    for (size_t k = 0; k < s_tx_n; k++)
        if (memcmp(mac, s_tx[k], 6) == 0) {
            kind = s_tx_kind[k];
            break;
        }
    portEXIT_CRITICAL(&s_mux);
    return kind;
}

size_t csi_capture_set_device_links(const uint8_t (*macs)[6], size_t n)
{
    portENTER_CRITICAL(&s_mux);
    size_t m = s_tx_n ? 1 : 0;  /* точка доступа остаётся первой */
    for (size_t k = 1; k < s_tx_n; k++) {  /* платы (ESP-NOW) остаются */
        if (s_tx_kind[k] != CSI_LINK_BOARD) continue;
        memmove(s_tx[m], s_tx[k], 6);
        s_tx_kind[m] = CSI_LINK_BOARD;
        s_tx_last[m] = s_tx_last[k];
        m++;
    }
    size_t added = 0;
    for (size_t i = 0; i < n && m > 0 && m < MAX_TX; i++) {
        bool dup = false;
        for (size_t k = 0; k < m && !dup; k++) dup = memcmp(s_tx[k], macs[i], 6) == 0;
        if (dup) continue;
        memcpy(s_tx[m], macs[i], 6);
        s_tx_kind[m] = CSI_LINK_DEVICE;
        s_tx_last[m] = 0;
        m++;
        added++;
    }
    s_tx_n = m;
    portEXIT_CRITICAL(&s_mux);
    ESP_LOGI(TAG, "device links: %u (%u transmitters)", (unsigned)added, (unsigned)m);
    return added;
}

esp_err_t csi_capture_set_promisc(bool on)
{
    if (!s_ring) return ESP_ERR_INVALID_STATE;
    esp_err_t err = ESP_OK;
    if (on) {
        /* Только одиночные кадры данных (MPDU): ответы устройств на пинг. Склеенные кадры (A-MPDU — чужие загрузки,
         * видео) через путь прослушивания роняли все платы разом (13.10: паника в wDev_SnifferRxData). */
        wifi_promiscuous_filter_t f = {.filter_mask = WIFI_PROMIS_FILTER_MASK_DATA_MPDU};
        esp_wifi_set_promiscuous_filter(&f);
    }
    err = esp_wifi_set_promiscuous(on);
    if (err == ESP_OK) s_promisc = on;
    ESP_LOGI(TAG, "promiscuous %s: %s", on ? "on" : "off", esp_err_to_name(err));
    return err;
}

bool csi_capture_promisc(void) { return s_promisc; }

esp_err_t csi_capture_start(const uint8_t bssid[6], uint8_t csi_source, const uint8_t (*links)[6], size_t n_links,
                            bool promisc)
{
    if (!s_flash_lock) s_flash_lock = xSemaphoreCreateMutexStatic(&s_flash_buf);  /* до задач uplink и опроса */
    memcpy(s_bssid, bssid, 6);
    memcpy(s_tx[0], bssid, 6);
    s_tx_kind[0] = CSI_LINK_AP;
    s_tx_n = 1;
    for (size_t i = 0; i < n_links && s_tx_n < MAX_TX; i++) {
        s_tx_kind[s_tx_n] = CSI_LINK_DEVICE;
        memcpy(s_tx[s_tx_n++], links[i], 6);
    }
    s_source = csi_source;
    s_ring = xRingbufferCreateWithCaps(RING_BYTES, RINGBUF_TYPE_NOSPLIT, MALLOC_CAP_SPIRAM);
    if (!s_ring)  /* плата без PSRAM (S3 с 8 МБ флеша): кольцо поменьше во внутренней памяти, ~1 с запаса */
        s_ring = xRingbufferCreateWithCaps(RING_BYTES_INTERNAL, RINGBUF_TYPE_NOSPLIT, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!s_ring) return ESP_ERR_NO_MEM;
    wifi_csi_config_t cfg = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = false,
        .ltf_merge_en = false,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
    if (s_tx_n > 1 && !promisc) ESP_LOGW(TAG, "promiscuous off: only the access point and ESP-NOW frames of other boards");
    if (s_tx_n > 1 && promisc) { /* кадры устройств дома (ответы на пинг платы) видны только в promiscuous */
        if (csi_capture_set_promisc(true) != ESP_OK) ESP_LOGE(TAG, "device links disabled");
        else ESP_LOGI(TAG, "%u device links", (unsigned)(s_tx_n - 1));
    }
    ESP_LOGI(TAG, "capture started, source=%s", csi_source ? "lltf" : "ht");
    return ESP_OK;
}

void csi_capture_pause(void)
{
    if (!s_ring) return;
    esp_wifi_set_promiscuous(false);
    esp_wifi_set_csi(false);
    ESP_LOGW(TAG, "capture paused (flash write)");
}

void csi_capture_resume(void)
{
    if (!s_ring) return;
    esp_wifi_set_csi(true);
    if (s_promisc) esp_wifi_set_promiscuous(true);
    ESP_LOGI(TAG, "capture resumed");
}

void csi_capture_flash_begin(void)
{
    if (s_flash_lock) xSemaphoreTake(s_flash_lock, portMAX_DELAY);
    csi_capture_pause();
}

void csi_capture_flash_end(void)
{
    csi_capture_resume();
    if (s_flash_lock) xSemaphoreGive(s_flash_lock);
}

void csi_capture_counters(csi_counters_t *out)
{
    portENTER_CRITICAL(&s_mux);
    *out = s_cnt;
    portEXIT_CRITICAL(&s_mux);
}

bool csi_capture_pop(wsn_csi_frame_t *out, int8_t *data_buf, TickType_t wait)
{
    size_t size = 0;
    uint8_t *item = xRingbufferReceive(s_ring, &size, wait);
    if (!item) return false;
    item_hdr_t h;
    memcpy(&h, item, sizeof(h));
    memcpy(data_buf, item + sizeof(h), h.len);
    vRingbufferReturnItem(s_ring, item);
    *out = (wsn_csi_frame_t){.t_us = h.t_us, .rssi = h.rssi, .noise = h.noise, .sig_mode = h.sig_mode,
                             .cwb = h.cwb, .len = h.len, .data = data_buf};
    memcpy(out->src, h.src, 6);
    return true;
}
