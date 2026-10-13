#include "wifi_mgr.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"

static const char *TAG = "wifi_mgr";

static esp_netif_t *s_netif;
static volatile wifi_state_t s_state = WIFI_ST_IDLE;
static volatile bool s_has_ip;
static esp_ip4_addr_t s_ip, s_gw;
static esp_timer_handle_t s_retry_timer;
static uint32_t s_backoff_s = 1;
static wifi_link_cb_t s_link_cb;
static bool s_enabled;
/* Плата привязана к одной точке доступа (BSSID). Если та не находится / не пускает LOCK_FAILS раз подряд
 * (плату унесли за стены), подключаемся к любой точке той же сети — самой сильной. Все узлы mesh на одном канале. */
#define LOCK_FAILS 5
static bool s_locked;
static int s_fails;

static void retry_cb(void *arg)
{
    s_state = WIFI_ST_CONNECTING;
    esp_wifi_connect();
}

static void schedule_retry(void)
{
    ESP_LOGW(TAG, "reconnect in %lus", (unsigned long)s_backoff_s);
    esp_timer_stop(s_retry_timer);
    esp_timer_start_once(s_retry_timer, (uint64_t)s_backoff_s * 1000000ULL);
    s_backoff_s = s_backoff_s >= 16 ? 30 : s_backoff_s * 2;
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_enabled) {
            s_state = WIFI_ST_CONNECTING;
            esp_wifi_connect();
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *d = data;
        bool was_up = s_has_ip;
        s_has_ip = false;
        switch (d->reason) {
        case WIFI_REASON_NO_AP_FOUND:
        case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
        case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
        case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
            s_state = WIFI_ST_BSSID_NOT_FOUND;
            break;
        case WIFI_REASON_AUTH_FAIL:
        case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
        case WIFI_REASON_HANDSHAKE_TIMEOUT:
            s_state = WIFI_ST_AUTH_FAIL;
            break;
        default:
            s_state = WIFI_ST_CONNECTING;
        }
        ESP_LOGW(TAG, "disconnected, reason %d -> %s", d->reason, wifi_mgr_state_str(s_state));
        if (was_up && s_link_cb) s_link_cb(false);
        if (!was_up && s_locked && ++s_fails >= LOCK_FAILS) {
            wifi_config_t cfg;
            if (esp_wifi_get_config(WIFI_IF_STA, &cfg) == ESP_OK) {
                cfg.sta.bssid_set = false;
                cfg.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
                if (esp_wifi_set_config(WIFI_IF_STA, &cfg) == ESP_OK) {  /* хранилище RAM: во флеш не пишет */
                    s_locked = false;
                    s_backoff_s = 1;
                    ESP_LOGW(TAG, "locked access point unreachable %d times: any node of the network", s_fails);
                }
            }
        }
        schedule_retry();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *e = data;
        s_ip = e->ip_info.ip;
        s_gw = e->ip_info.gw;
        s_has_ip = true;
        s_state = WIFI_ST_CONNECTED;
        s_backoff_s = 1;
        s_fails = 0;
        ESP_LOGI(TAG, "got ip " IPSTR " gw " IPSTR, IP2STR(&s_ip), IP2STR(&s_gw));
        if (s_link_cb) s_link_cb(true);
    }
}

esp_err_t wifi_mgr_start(const wsn_settings_t *s)
{
    s_netif = esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    const esp_timer_create_args_t targs = {.callback = retry_cb, .name = "wifi_retry"};
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_retry_timer));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    s_enabled = settings_complete(s);
    if (s_enabled) {
        wifi_config_t cfg = {0};
        strlcpy((char *)cfg.sta.ssid, s->ssid, sizeof(cfg.sta.ssid));
        strlcpy((char *)cfg.sta.password, s->pass, sizeof(cfg.sta.password));
        memcpy(cfg.sta.bssid, s->bssid, 6);
        cfg.sta.bssid_set = true;
        s_locked = true;
        cfg.sta.channel = 0;
        cfg.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
        cfg.sta.pmf_cfg.capable = true;
        esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &cfg);
        if (err != ESP_OK) { /* не падать в цикл перезагрузок: консоль должна остаться доступной */
            ESP_LOGE(TAG, "wifi config rejected (%s): fix with 'wifi set' and reboot", esp_err_to_name(err));
            s_enabled = false;
        }
    } else {
        ESP_LOGW(TAG, "settings incomplete: use console (wifi set / server set / key set)");
    }
    ESP_ERROR_CHECK(esp_wifi_start());
    wifi_country_t country = {.cc = "RU", .schan = 1, .nchan = 13, .policy = WIFI_COUNTRY_POLICY_MANUAL};
    esp_wifi_set_country(&country);
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    return ESP_OK;
}

wifi_state_t wifi_mgr_state(void) { return s_state; }

const char *wifi_mgr_state_str(wifi_state_t st)
{
    static const char *names[] = {"IDLE", "CONNECTING", "CONNECTED", "BSSID_NOT_FOUND", "AUTH_FAIL"};
    return (unsigned)st < 5 ? names[st] : "?";
}

bool wifi_mgr_gateway(esp_ip4_addr_t *gw)
{
    if (!s_has_ip) return false;
    *gw = s_gw;
    return true;
}

bool wifi_mgr_ip(esp_ip4_addr_t *ip)
{
    if (!s_has_ip) return false;
    *ip = s_ip;
    return true;
}

int wifi_mgr_rssi(void)
{
    wifi_ap_record_t ap;
    if (!s_has_ip || esp_wifi_sta_get_ap_info(&ap) != ESP_OK) return 0;
    return ap.rssi;
}

void wifi_mgr_set_link_cb(wifi_link_cb_t cb) { s_link_cb = cb; }
