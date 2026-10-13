#include <string.h>
#include <stdio.h>
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

#include "esp_system.h"
#include "esp_app_desc.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "csi_capture.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/usb_serial_jtag.h"

static const char *TAG = "app";

/* Причины последних 16 перезапусков — в NVS при старте (до Wi-Fi: запись во флеш не мешает приёму CSI).
 * Плата, которая не подключается вдали от сервера, по возвращении покажет командой diag: питание
 * (BROWNOUT), падение (PANIC), сторожевой таймер (WDT) или просто перезапуски по питанию (POWERON). */
#define DIAG_N 16
static uint8_t s_diag[DIAG_N];
static uint8_t s_stage[DIAG_N];  /* до какого шага дошла каждая загрузка; бит 0x80 = на USB есть компьютер */
static uint32_t s_diag_n;

static void diag_record(void)
{
    nvs_handle_t h;
    if (nvs_open("diag", NVS_READWRITE, &h) != ESP_OK) return;
    size_t len = sizeof(s_diag);
    if (nvs_get_blob(h, "rr", s_diag, &len) != ESP_OK) memset(s_diag, 0, sizeof(s_diag));
    nvs_get_u32(h, "n", &s_diag_n);
    len = sizeof(s_stage);
    if (nvs_get_blob(h, "st", s_stage, &len) != ESP_OK) memset(s_stage, 0, sizeof(s_stage));
    memmove(s_stage + 1, s_stage, DIAG_N - 1);
    s_stage[0] = 1;
    nvs_set_blob(h, "st", s_stage, sizeof(s_stage));
    memmove(s_diag + 1, s_diag, DIAG_N - 1);
    s_diag[0] = (uint8_t)esp_reset_reason();
    s_diag_n++;
    nvs_set_blob(h, "rr", s_diag, sizeof(s_diag));
    nvs_set_u32(h, "n", s_diag_n);
    nvs_commit(h);
    nvs_close(h);
}

extern volatile bool wsn_flash_quiet;

void diag_stage(uint8_t st)
{
    if (wsn_flash_quiet) return;
    uint8_t v = st | (usb_serial_jtag_is_connected() ? 0x80 : 0);
    if (s_stage[0] == v) return;
    s_stage[0] = v;
    nvs_handle_t h;
    if (nvs_open("diag", NVS_READWRITE, &h) != ESP_OK) return;
    nvs_set_blob(h, "st", s_stage, sizeof(s_stage));
    nvs_commit(h);
    nvs_close(h);
}

const char *diag_reason_str(uint8_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_EXT: return "EXT";
    case ESP_RST_USB: return "USB";
    case ESP_RST_JTAG: return "JTAG";
    case 0: return "-";
    default: return "OTHER";
    }
}

void diag_print(void)
{
    printf("DIAG boots=%lu last:", (unsigned long)s_diag_n);
    for (int i = 0; i < DIAG_N && s_diag[i]; i++)
        printf(" %s/%u%s", diag_reason_str(s_diag[i]), s_stage[i] & 0x7f, (s_stage[i] & 0x80) ? "u" : "");
    printf("\n");
}
static wsn_settings_t s_settings;

uint32_t wsn_fw_id(void)
{
    const uint8_t *h = esp_app_get_description()->app_elf_sha256;
    return 0x80000000u | ((uint32_t)h[0] << 16) | ((uint32_t)h[1] << 8) | h[2];
}

/* Обновление по воздуху: tools/push_fw.sh.
 * Прошивка, пришедшая по воздуху, сначала «на проверке»: если она не дойдёт до Wi-Fi за FW_CONFIRM_S, плата
 * перезапускается, и загрузчик возвращает прежнюю. Подтверждение — при первом получении IP. */
#define FW_CONFIRM_S 120
static bool s_fw_pending;
static esp_timer_handle_t s_fw_timer;

static void fw_confirm_timeout(void *arg)
{
    ESP_LOGE(TAG, "new firmware did not reach Wi-Fi in %d s: restarting, the bootloader rolls back", FW_CONFIRM_S);
    esp_restart();
}

static void fw_check_pending(void)
{
    esp_ota_img_states_t st;
    const esp_partition_t *run = esp_ota_get_running_partition();
    if (run && esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
        s_fw_pending = true;
        const esp_timer_create_args_t a = {.callback = fw_confirm_timeout, .name = "fw_confirm"};
        if (esp_timer_create(&a, &s_fw_timer) == ESP_OK) esp_timer_start_once(s_fw_timer, FW_CONFIRM_S * 1000000ULL);
        ESP_LOGW(TAG, "firmware %08lx from the air: waiting for Wi-Fi to confirm it", (unsigned long)wsn_fw_id());
    }
}

static void fw_confirm(void)
{
    if (!s_fw_pending) return;
    s_fw_pending = false;
    if (s_fw_timer) esp_timer_stop(s_fw_timer);
    csi_capture_pause();  /* подтверждение пишет во флеш */
    esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
    csi_capture_resume();
    ESP_LOGI(TAG, "new firmware confirmed: %s", esp_err_to_name(err));
}

static bool usb_host_wait(int ms)
{
    for (int t = 0; t < ms; t += 50) {
        if (usb_serial_jtag_is_connected()) return true;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    return usb_serial_jtag_is_connected();
}

static void console_when_host_task(void *arg)
{
    while (!usb_serial_jtag_is_connected()) vTaskDelay(pdMS_TO_TICKS(1000));
    vTaskDelay(pdMS_TO_TICKS(300));
    if (console_start(&s_settings, uplink_csi_hz) != ESP_OK) ESP_LOGE(TAG, "console start failed");
    vTaskDelete(NULL);
}

static void on_link(bool up)
{
    esp_ip4_addr_t gw;
    if (up) fw_confirm();
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
    diag_record();
    fw_check_pending();
    ESP_LOGI(TAG, "reset reason %s", diag_reason_str(s_diag[0]));
    ESP_ERROR_CHECK(settings_load(&s_settings));
    diag_stage(2);
    uint32_t boot_id = settings_next_boot_id();
    diag_stage(3);
    /* Консоль на USB запускается только при подключённом компьютере: без хоста (плата на зарядке)
     * её запуск зависал, и плата не доходила до Wi-Fi (журнал diag: все загрузки от зарядника стояли на шаге 3).
     * Если компьютера нет, консоль поднимет фоновая задача, когда он появится. */
    if (usb_host_wait(2000)) {
        ESP_ERROR_CHECK(console_start(&s_settings, uplink_csi_hz));
    } else {
        xTaskCreate(console_when_host_task, "console_wait", 3072, NULL, 1, NULL);
    }
    diag_stage(4);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    bool complete = settings_complete(&s_settings);
    wifi_mgr_set_link_cb(on_link);
    diag_stage(5);
    err = wifi_mgr_start(&s_settings);
    diag_stage(6);
    warn_if(err, "wifi start");
    if (complete && err == ESP_OK && boot_id != 0) {
        wsn_flash_quiet = true;
        err = csi_capture_start(s_settings.bssid, s_settings.csi_source, s_settings.links, s_settings.n_links);
        warn_if(err, "csi capture");
        if (err == ESP_OK) warn_if(uplink_start(&s_settings, boot_id), "uplink");
        if (err == ESP_OK) warn_if(model_ota_start(), "model OTA");
        if (err == ESP_OK) warn_if(infer_start(), "model (CSI streaming continues without it)");
    } else if (complete && boot_id == 0) {
        ESP_LOGE(TAG, "boot counter unavailable (NVS): uplink disabled");
    }
    ESP_LOGI(TAG, "fw %d (%08lx), boot %lu, settings %s", WSN_FW_VER, (unsigned long)wsn_fw_id(), (unsigned long)boot_id,
             complete ? "complete" : "incomplete");
}
