#include "console_cmds.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "settings.h"
#include "wifi_mgr.h"

static const char *TAG = "app";
static wsn_settings_t s_settings;

static float csi_hz_stub(void) { return 0.0f; }

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }
    ESP_ERROR_CHECK(settings_load(&s_settings));
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(wifi_mgr_start(&s_settings));
    ESP_LOGI(TAG, "fw %d, settings %s", WSN_FW_VER, settings_complete(&s_settings) ? "complete" : "incomplete");
    ESP_ERROR_CHECK(console_start(&s_settings, csi_hz_stub));
}
