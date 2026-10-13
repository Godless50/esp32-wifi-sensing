#include "console_cmds.h"
#include "csi_capture.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "infer.h"
#include "model_ota.h"
#include "nvs_flash.h"
#include "ping_gen.h"
#include "settings.h"
#include "uplink.h"
#include "wifi_mgr.h"

static const char *TAG = "app";
static wsn_settings_t s_settings;

static void on_link(bool up)
{
    esp_ip4_addr_t gw;
    if (up && wifi_mgr_gateway(&gw)) {
        ping_gen_start(gw, s_settings.ping_hz);
    } else {
        ping_gen_stop();
    }
}

static void warn_if(esp_err_t err, const char *what)
{
    if (err != ESP_OK) ESP_LOGE(TAG, "%s failed: %s (console still available)", what, esp_err_to_name(err));
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    ESP_ERROR_CHECK(settings_load(&s_settings));
    uint32_t boot_id = settings_next_boot_id();
    /* Console first: a bad Wi-Fi/CSI setup must never lock the user out of fixing it. */
    ESP_ERROR_CHECK(console_start(&s_settings, uplink_csi_hz));
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    bool complete = settings_complete(&s_settings);
    wifi_mgr_set_link_cb(on_link);
    err = wifi_mgr_start(&s_settings);
    warn_if(err, "wifi start");
    if (complete && err == ESP_OK && boot_id != 0) {
        err = csi_capture_start(s_settings.bssid, s_settings.csi_source, s_settings.links, s_settings.n_links);
        warn_if(err, "csi capture");
        if (err == ESP_OK) warn_if(uplink_start(&s_settings, boot_id), "uplink");
        if (err == ESP_OK) warn_if(model_ota_start(), "model OTA");
        if (err == ESP_OK) warn_if(infer_start(), "model (CSI streaming continues without it)");
    } else if (complete && boot_id == 0) {
        ESP_LOGE(TAG, "boot counter unavailable (NVS): uplink disabled");
    }
    ESP_LOGI(TAG, "fw %d, boot %lu, settings %s", WSN_FW_VER, (unsigned long)boot_id,
             complete ? "complete" : "incomplete");
}
