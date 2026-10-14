/* Board-to-board links without the Wi-Fi sniffer.
 *
 * The Wi-Fi library panics in wDev_SnifferRxData (memcpy with length -156, "Cache disabled but cached memory
 * region accessed") while promiscuous mode is on — on IDF 5.4 and 5.5 alike, more often the more frames it
 * sniffs. A broadcast ESP-NOW frame is received by every board on the channel through the normal receive path,
 * and CSI is reported for it: each board broadcasts a tiny frame at beacon_hz, the others measure the link
 * "this board -> me" with promiscuous mode off. HT rate (MCS0), so the frames carry an HT-LTF like the rest. */
#include "espnow_beacon.h"

#include <string.h>

#include "csi_capture.h"
#include "esp_log.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "espnow";
static const uint8_t BCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
static TaskHandle_t s_task;
static uint16_t s_hz;

static void beacon_task(void *arg)
{
    uint8_t payload[8] = {'W', 'S', 'N', 'B'};
    uint32_t n = 0;
    TickType_t last = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(1000 / s_hz) ? pdMS_TO_TICKS(1000 / s_hz) : 1;
    for (;;) {
        memcpy(payload + 4, &n, 4);
        n++;
        esp_now_send(BCAST, payload, sizeof(payload));  /* ошибки (очередь полна) не важны: следующий кадр уйдёт */
        vTaskDelayUntil(&last, period);
    }
}

/* Маяк другой платы ("WSNB" + счётчик): её линию "та плата -> я" берём в захват CSI (до WSN_MAX_LINKS). */
static void on_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    if (info && data && len == 8 && memcmp(data, "WSNB", 4) == 0) csi_capture_add_link(info->src_addr);
}

esp_err_t espnow_beacon_start(uint16_t hz)
{
    if (s_task || hz == 0) return ESP_OK;
    s_hz = hz > 100 ? 100 : hz;
    esp_err_t err = esp_now_init();
    if (err != ESP_OK && err != ESP_ERR_ESPNOW_EXIST) return err;
    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, BCAST, 6);
    peer.ifidx = WIFI_IF_STA;
    peer.channel = 0;  /* текущий канал точки доступа */
    peer.encrypt = false;
    err = esp_now_add_peer(&peer);
    if (err != ESP_OK && err != ESP_ERR_ESPNOW_EXIST) return err;
    esp_now_register_recv_cb(on_recv);  /* соседние платы находятся сами: настраивать линии вручную не нужно */
    esp_now_rate_config_t rc = {.phymode = WIFI_PHY_MODE_HT20, .rate = WIFI_PHY_RATE_MCS0_LGI};
    if (esp_now_set_peer_rate_config(BCAST, &rc) != ESP_OK) ESP_LOGW(TAG, "HT rate not set (legacy frames)");
    if (xTaskCreatePinnedToCore(beacon_task, "espnow_beacon", 2048, NULL, 4, &s_task, 0) != pdPASS) return ESP_ERR_NO_MEM;
    ESP_LOGI(TAG, "broadcasting at %u Hz (board-to-board links without the sniffer)", s_hz);
    return ESP_OK;
}
