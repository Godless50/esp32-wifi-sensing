/* ICMP echo requests to the gateway at a fixed rate; every reply is a frame from the access point, so it carries
 * CSI of the link "access point -> board".
 *
 * The rate does not depend on the round trip: requests go out on a timer and replies are only drained. esp_ping
 * (used before) waits for each reply, so through a mesh node (round trip 30-40 ms) 100 Hz became ~25 Hz, and it
 * was restarted every 10 s (a new task each time) by the stall check. Here one task lives for the whole boot. */
#include "ping_gen.h"

#include <errno.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet_chksum.h"
#include "lwip/prot/icmp.h"
#include "lwip/sockets.h"

static const char *TAG = "ping_gen";

#define PAYLOAD 16

static TaskHandle_t s_task;
static SemaphoreHandle_t s_lock;
static volatile uint32_t s_gw;  /* 0 = stopped */
static volatile uint16_t s_hz;
static volatile uint32_t s_gen; /* bumped on every start: the task restarts its timing */

static void ping_task(void *arg)
{
    int sock = -1;
    uint32_t gen = 0;
    uint16_t seq = 0;
    uint8_t pkt[sizeof(struct icmp_echo_hdr) + PAYLOAD];
    uint8_t rx[128];
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        if (!s_gw) {
            if (sock >= 0) {
                close(sock);
                sock = -1;
            }
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
            continue;
        }
        if (sock < 0) {
            sock = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
            if (sock < 0) {
                ESP_LOGW(TAG, "raw socket: errno %d, retry in 1 s", errno);
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
        }
        if (gen != s_gen) {
            gen = s_gen;
            last = xTaskGetTickCount();
        }
        struct icmp_echo_hdr *h = (struct icmp_echo_hdr *)pkt;
        memset(pkt, 0, sizeof(pkt));
        h->type = ICMP_ECHO;
        h->id = 0x5753; /* "WS" */
        h->seqno = lwip_htons(++seq);
        for (int i = 0; i < PAYLOAD; i++) pkt[sizeof(*h) + i] = (uint8_t)i;
        h->chksum = inet_chksum(pkt, sizeof(pkt));
        struct sockaddr_in to = {.sin_family = AF_INET, .sin_addr.s_addr = s_gw};
        sendto(sock, pkt, sizeof(pkt), 0, (struct sockaddr *)&to, sizeof(to)); /* очередь полна — уйдёт следующий */
        while (recv(sock, rx, sizeof(rx), MSG_DONTWAIT) > 0) { /* ответы нужны радио (CSI), не программе */
        }
        uint16_t hz = s_hz ? s_hz : 1;
        TickType_t period = pdMS_TO_TICKS(1000 / hz);
        vTaskDelayUntil(&last, period ? period : 1);
    }
}

esp_err_t ping_gen_start(esp_ip4_addr_t gw, uint16_t hz)
{
    if (!s_lock) s_lock = xSemaphoreCreateMutex(); /* первый вызов — из обработчика событий Wi-Fi, до uplink */
    if (!s_lock) return ESP_ERR_NO_MEM;
    if (hz < 1) hz = 1;
    if (hz > 200) hz = 200;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_hz = hz;
    s_gw = gw.addr;
    s_gen++;
    esp_err_t err = ESP_OK;
    if (!s_task && xTaskCreatePinnedToCore(ping_task, "ping_gen", 3072, NULL, 5, &s_task, 1) != pdPASS) {
        s_task = NULL;
        err = ESP_ERR_NO_MEM;
    }
    if (s_task) xTaskNotifyGive(s_task);
    xSemaphoreGive(s_lock);
    if (err == ESP_OK) ESP_LOGI(TAG, "pinging " IPSTR " at %u Hz", IP2STR(&gw), hz);
    return err;
}

void ping_gen_stop(void)
{
    s_gw = 0;
}

void ping_gen_set_hz(uint16_t hz)
{
    if (hz < 1) hz = 1;
    if (hz > 200) hz = 200;
    if (hz != s_hz) {
        s_hz = hz;
        s_gen++; /* отсчёт периода заново */
        ESP_LOGI(TAG, "rate %u Hz", hz);
    }
}
