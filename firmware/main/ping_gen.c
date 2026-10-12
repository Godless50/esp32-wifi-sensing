#include "ping_gen.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lwip/ip_addr.h"
#include "ping/ping_sock.h"

static const char *TAG = "ping_gen";
static esp_ping_handle_t s_ping;
static SemaphoreHandle_t s_lock;

static void ensure_lock(void)
{
    if (!s_lock) s_lock = xSemaphoreCreateMutex(); /* first call happens before concurrency */
}

static void stop_locked(void)
{
    if (s_ping) {
        esp_ping_stop(s_ping);
        esp_ping_delete_session(s_ping);
        s_ping = NULL;
    }
}

esp_err_t ping_gen_start(esp_ip4_addr_t gw, uint16_t hz)
{
    ensure_lock();
    if (hz < 1) hz = 1;
    if (hz > 200) hz = 200;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    stop_locked();
    esp_ping_config_t cfg = ESP_PING_DEFAULT_CONFIG();
    ip_addr_t target = {0};
    target.type = IPADDR_TYPE_V4;
    target.u_addr.ip4.addr = gw.addr;
    cfg.target_addr = target;
    cfg.count = ESP_PING_COUNT_INFINITE;
    cfg.interval_ms = 1000 / hz;
    /* esp_ping waits for the reply before the next echo: a long timeout turns one lost reply into a gap */
    cfg.timeout_ms = cfg.interval_ms * 2 < 20 ? 20 : cfg.interval_ms * 2;
    cfg.data_size = 16;
    cfg.task_stack_size = 3072;
    esp_ping_callbacks_t cbs = {0};
    esp_err_t err = esp_ping_new_session(&cfg, &cbs, &s_ping);
    if (err == ESP_OK) err = esp_ping_start(s_ping);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "start failed: %s", esp_err_to_name(err));
        stop_locked();
    } else {
        ESP_LOGI(TAG, "pinging " IPSTR " at %u Hz", IP2STR(&gw), hz);
    }
    xSemaphoreGive(s_lock);
    return err;
}

void ping_gen_stop(void)
{
    ensure_lock();
    xSemaphoreTake(s_lock, portMAX_DELAY);
    stop_locked();
    xSemaphoreGive(s_lock);
}
