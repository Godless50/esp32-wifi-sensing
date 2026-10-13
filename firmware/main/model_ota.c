/* Model update over the network — see model_ota.h. */
#include "model_ota.h"

#include <string.h>

#include "esp_log.h"
#include "esp_partition.h"
#include "esp_rom_crc.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs.h"

static const char *TAG = "model_ota";

#define NS "wsn_model"
#define SECTOR 4096
#define HDR 128

typedef struct {
    uint32_t version, total, offset;
    uint16_t len;
    uint8_t data[WSN_MODEL_CHUNK_MAX];
} item_t;

static QueueHandle_t s_q;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static wsn_model_ack_t s_st;
static bool s_changed;
static uint32_t s_erased;  /* граница стёртой области в целевом разделе */
static uint8_t s_active;

static void set_state(uint32_t ver, uint32_t received, uint8_t state, uint8_t error)
{
    portENTER_CRITICAL(&s_mux);
    s_st.version = ver;
    s_st.received = received;
    s_st.state = state;
    s_st.slot = (uint8_t)(s_active ^ 1);
    s_st.error = error;
    s_changed = true;
    portEXIT_CRITICAL(&s_mux);
}

static const esp_partition_t *slot_part(uint8_t slot)
{
    return esp_partition_find_first((esp_partition_type_t)0x40, (esp_partition_subtype_t)slot, slot ? "model_b" : "model_a");
}

static bool verify(const esp_partition_t *p, uint32_t total)
{
    const void *map = NULL;
    esp_partition_mmap_handle_t h;
    if (esp_partition_mmap(p, 0, p->size, ESP_PARTITION_MMAP_DATA, &map, &h) != ESP_OK) return false;
    const uint8_t *b = (const uint8_t *)map;
    uint32_t len, crc;
    memcpy(&len, b + 12, 4);
    memcpy(&crc, b + 16, 4);
    bool ok = memcmp(b, "WSNM", 4) == 0 && len + HDR == total && esp_rom_crc32_le(0, b + HDR, len) == crc;
    esp_partition_munmap(h);
    return ok;
}

static void ota_task(void *arg)
{
    static item_t it;
    uint32_t ver = 0, total = 0, received = 0;
    const esp_partition_t *dst = slot_part(s_active ^ 1);
    for (;;) {
        if (xQueueReceive(s_q, &it, portMAX_DELAY) != pdTRUE) continue;
        if (!dst) { set_state(it.version, 0, WSN_OTA_ERROR, 1); continue; }
        if (it.version != ver || it.offset == 0) {  /* новая загрузка */
            if (it.offset != 0) continue;            /* ждём начало */
            if (it.total < HDR || it.total > dst->size) { set_state(it.version, 0, WSN_OTA_ERROR, 2); continue; }
            ver = it.version; total = it.total; received = 0; s_erased = 0;
            ESP_LOGI(TAG, "receiving model v%lu, %lu bytes -> %s", (unsigned long)ver, (unsigned long)total, dst->label);
        }
        if (it.offset != received || it.total != total) continue;  /* только по порядку; пропуск сервер дошлёт */
        uint32_t end = it.offset + it.len;
        while (s_erased < end) {
            if (esp_partition_erase_range(dst, s_erased, SECTOR) != ESP_OK) { set_state(ver, received, WSN_OTA_ERROR, 3); break; }
            s_erased += SECTOR;
        }
        if (s_erased < end) continue;
        if (esp_partition_write(dst, it.offset, it.data, it.len) != ESP_OK) { set_state(ver, received, WSN_OTA_ERROR, 4); continue; }
        received = end;
        if (received < total) { set_state(ver, received, WSN_OTA_RECEIVING, 0); continue; }
        if (!verify(dst, total)) { set_state(ver, received, WSN_OTA_ERROR, 5); ver = 0; continue; }
        nvs_handle_t h;
        if (nvs_open(NS, NVS_READWRITE, &h) == ESP_OK) {
            nvs_set_u8(h, "slot", (uint8_t)(s_active ^ 1));
            nvs_commit(h);
            nvs_close(h);
        }
        ESP_LOGI(TAG, "model v%lu verified, switching to %s and restarting", (unsigned long)ver, dst->label);
        set_state(ver, received, WSN_OTA_ACTIVATING, 0);
        vTaskDelay(pdMS_TO_TICKS(1500));  /* успеть отправить ACK */
        esp_restart();
    }
}

uint8_t model_ota_active_slot(void)
{
    uint8_t slot = 0;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) == ESP_OK) {
        nvs_get_u8(h, "slot", &slot);
        nvs_close(h);
    }
    return slot ? 1 : 0;
}

esp_err_t model_ota_start(void)
{
    s_active = model_ota_active_slot();
    s_st.slot = (uint8_t)(s_active ^ 1);
    s_q = xQueueCreate(4, sizeof(item_t));
    if (!s_q) return ESP_ERR_NO_MEM;
    return xTaskCreatePinnedToCore(ota_task, "model_ota", 4096, NULL, 3, NULL, 0) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

void model_ota_feed(const wsn_model_chunk_t *c)
{
    static item_t it;  /* вызывается только из задачи uplink */
    if (!s_q || c->len > WSN_MODEL_CHUNK_MAX) return;
    it.version = c->version; it.total = c->total; it.offset = c->offset; it.len = c->len;
    memcpy(it.data, c->data, c->len);
    xQueueSend(s_q, &it, 0);
}

bool model_ota_status(wsn_model_ack_t *out)
{
    portENTER_CRITICAL(&s_mux);
    *out = s_st;
    bool ch = s_changed;
    s_changed = false;
    portEXIT_CRITICAL(&s_mux);
    return ch;
}
