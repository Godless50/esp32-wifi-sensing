// Probe firmware (throwaway): scan APs, then sniff every 2.4 GHz channel
// counting frames / CSI callbacks per transmitter MAC.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "nvs_flash.h"

#define MAX_TX      96
#define DWELL_MS    3000
#define MACSTR_F    "%02x:%02x:%02x:%02x:%02x:%02x"
#define MAC_A(m)    (m)[0], (m)[1], (m)[2], (m)[3], (m)[4], (m)[5]

typedef struct {
    uint8_t  mac[6];
    uint32_t frames, beacons, data, mgmt, csi;
    uint16_t csi_len;
    uint8_t  csi_ht;
    int32_t  rssi_sum;
} tx_stat_t;

static tx_stat_t    s_tab[MAX_TX];
static int          s_n;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;

// caller must hold s_mux
static tx_stat_t *lookup(const uint8_t *mac)
{
    for (int i = 0; i < s_n; i++) {
        if (memcmp(s_tab[i].mac, mac, 6) == 0) return &s_tab[i];
    }
    if (s_n >= MAX_TX) return NULL;
    tx_stat_t *t = &s_tab[s_n++];
    memset(t, 0, sizeof(*t));
    memcpy(t->mac, mac, 6);
    return t;
}

static void promisc_cb(void *buf, wifi_promiscuous_pkt_type_t type)
{
    const wifi_promiscuous_pkt_t *p = (const wifi_promiscuous_pkt_t *)buf;
    if (p->rx_ctrl.sig_len < 24) return;
    const uint8_t *hdr = p->payload;
    uint8_t ftype = (hdr[0] >> 2) & 0x3;
    uint8_t subtype = (hdr[0] >> 4) & 0xF;
    if (ftype == 1) return;                 // control frames: no reliable addr2
    const uint8_t *ta = hdr + 10;           // addr2 = transmitter
    portENTER_CRITICAL(&s_mux);
    tx_stat_t *t = lookup(ta);
    if (t) {
        t->frames++;
        t->rssi_sum += p->rx_ctrl.rssi;
        if (ftype == 0) {
            t->mgmt++;
            if (subtype == 8) t->beacons++;
        } else if (ftype == 2) {
            t->data++;
        }
    }
    portEXIT_CRITICAL(&s_mux);
}

static void csi_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info) return;
    portENTER_CRITICAL(&s_mux);
    tx_stat_t *t = lookup(info->mac);
    if (t) {
        t->csi++;
        t->csi_len = info->len;
        t->csi_ht = info->rx_ctrl.sig_mode;
    }
    portEXIT_CRITICAL(&s_mux);
}

static const char *auth_str(wifi_auth_mode_t m)
{
    switch (m) {
    case WIFI_AUTH_OPEN: return "OPEN";
    case WIFI_AUTH_WEP: return "WEP";
    case WIFI_AUTH_WPA_PSK: return "WPA";
    case WIFI_AUTH_WPA2_PSK: return "WPA2";
    case WIFI_AUTH_WPA_WPA2_PSK: return "WPA/WPA2";
    case WIFI_AUTH_WPA3_PSK: return "WPA3";
    case WIFI_AUTH_WPA2_WPA3_PSK: return "WPA2/WPA3";
    case WIFI_AUTH_WPA2_ENTERPRISE: return "WPA2-ENT";
    default: return "OTHER";
    }
}

static void do_scan(void)
{
    wifi_scan_config_t cfg = {
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time.active.min = 120,
        .scan_time.active.max = 400,
    };
    ESP_ERROR_CHECK(esp_wifi_scan_start(&cfg, true));
    uint16_t n = 0;
    esp_wifi_scan_get_ap_num(&n);
    if (n > 64) n = 64;
    wifi_ap_record_t *rec = calloc(n ? n : 1, sizeof(wifi_ap_record_t));
    if (!rec) return;
    esp_wifi_scan_get_ap_records(&n, rec);
    printf("SCAN_BEGIN,%u\n", n);
    for (int i = 0; i < n; i++) {
        printf("SCAN," MACSTR_F ",%s,%u,%d,%s,%s%s%s\n",
               MAC_A(rec[i].bssid), (char *)rec[i].ssid, rec[i].primary,
               rec[i].rssi, auth_str(rec[i].authmode),
               rec[i].phy_11b ? "b" : "", rec[i].phy_11g ? "g" : "",
               rec[i].phy_11n ? "n" : "");
    }
    printf("SCAN_END\n");
    free(rec);
}

static void do_sniff(void)
{
    static tx_stat_t snap[MAX_TX];
    wifi_promiscuous_filter_t flt = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA,
    };
    wifi_csi_config_t csi_cfg = {
        .lltf_en = true,
        .htltf_en = true,
        .stbc_htltf2_en = true,
        .ltf_merge_en = true,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&flt));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(promisc_cb));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(csi_cb, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));

    for (uint8_t ch = 1; ch <= 13; ch++) {
        portENTER_CRITICAL(&s_mux);
        s_n = 0;
        portEXIT_CRITICAL(&s_mux);
        if (esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE) != ESP_OK) {
            printf("SNIFF_ERR,%u\n", ch);
            continue;
        }
        portENTER_CRITICAL(&s_mux);
        s_n = 0;
        portEXIT_CRITICAL(&s_mux);
        vTaskDelay(pdMS_TO_TICKS(DWELL_MS));
        portENTER_CRITICAL(&s_mux);
        int n = s_n;
        memcpy(snap, s_tab, sizeof(tx_stat_t) * n);
        portEXIT_CRITICAL(&s_mux);
        uint32_t total = 0;
        for (int i = 0; i < n; i++) total += snap[i].frames;
        printf("CH,%u,tx=%d,frames_per_s=%.1f\n", ch, n, total * 1000.0f / DWELL_MS);
        for (int i = 0; i < n; i++) {
            if (snap[i].frames < 3) continue;
            printf("SNIFF,%u," MACSTR_F ",fps=%.1f,beacon_ps=%.1f,data_ps=%.1f,csi_ps=%.1f,csi_len=%u,ht=%u,rssi=%ld\n",
                   ch, MAC_A(snap[i].mac),
                   snap[i].frames * 1000.0f / DWELL_MS,
                   snap[i].beacons * 1000.0f / DWELL_MS,
                   snap[i].data * 1000.0f / DWELL_MS,
                   snap[i].csi * 1000.0f / DWELL_MS,
                   snap[i].csi_len, snap[i].csi_ht,
                   (long)(snap[i].rssi_sum / (int32_t)snap[i].frames));
        }
        fflush(stdout);
    }
    esp_wifi_set_csi(false);
    esp_wifi_set_promiscuous(false);
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    wifi_country_t country = {
        .cc = "RU",
        .schan = 1,
        .nchan = 13,
        .policy = WIFI_COUNTRY_POLICY_MANUAL,
    };
    err = esp_wifi_set_country(&country);
    printf("COUNTRY,%s\n", esp_err_to_name(err));
    esp_log_level_set("wifi", ESP_LOG_WARN);

    for (uint32_t round = 1;; round++) {
        printf("ROUND_BEGIN,%lu\n", (unsigned long)round);
        do_scan();
        do_sniff();
        printf("ROUND_END,%lu\n", (unsigned long)round);
        fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
