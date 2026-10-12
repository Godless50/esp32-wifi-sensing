#include "csi_capture.h"

#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/ringbuf.h"

static const char *TAG = "csi";

typedef struct __attribute__((packed)) {
    uint64_t t_us;
    int8_t rssi, noise;
    uint8_t sig_mode, cwb;
    uint16_t len;
} item_hdr_t;

#define RING_BYTES (160 * 1024) /* ~4 s at 100 Hz x 400 B */

static RingbufHandle_t s_ring;
static uint8_t s_bssid[6];
static uint8_t s_source;
static csi_counters_t s_cnt;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

static void csi_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || memcmp(info->mac, s_bssid, 6) != 0) return;
    uint16_t len = info->len > CSI_MAX_LEN ? CSI_MAX_LEN : info->len;
    const wifi_pkt_rx_ctrl_t *rx = &info->rx_ctrl;
    bool match = s_source == 1 || (rx->sig_mode == 1 && len >= 256);

    item_hdr_t h = {.t_us = (uint64_t)esp_timer_get_time(), .rssi = rx->rssi, .noise = rx->noise_floor,
                    .sig_mode = rx->sig_mode, .cwb = rx->cwb, .len = len};
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
    } else {
        s_cnt.dropped++;
    }
    s_cnt.rssi_sum += rx->rssi;
    s_cnt.rssi_n++;
    portEXIT_CRITICAL(&s_mux);
}

esp_err_t csi_capture_start(const uint8_t bssid[6], uint8_t csi_source)
{
    memcpy(s_bssid, bssid, 6);
    s_source = csi_source;
    s_ring = xRingbufferCreateWithCaps(RING_BYTES, RINGBUF_TYPE_NOSPLIT, MALLOC_CAP_SPIRAM);
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
    ESP_LOGI(TAG, "capture started, source=%s", csi_source ? "lltf" : "ht");
    return ESP_OK;
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
    return true;
}
