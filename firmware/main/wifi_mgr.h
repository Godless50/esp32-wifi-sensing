#pragma once
#include <stdbool.h>

#include "esp_err.h"
#include "esp_netif_ip_addr.h"
#include "settings.h"

typedef enum {
    WIFI_ST_IDLE = 0,
    WIFI_ST_CONNECTING = 1,
    WIFI_ST_CONNECTED = 2,
    WIFI_ST_BSSID_NOT_FOUND = 3,
    WIFI_ST_AUTH_FAIL = 4,
} wifi_state_t;

typedef void (*wifi_link_cb_t)(bool up);

/* Init Wi-Fi driver + STA netif (always), and connect if settings are complete. */
esp_err_t wifi_mgr_start(const wsn_settings_t *s);
wifi_state_t wifi_mgr_state(void);
const char *wifi_mgr_state_str(wifi_state_t st);
bool wifi_mgr_gateway(esp_ip4_addr_t *gw);
bool wifi_mgr_ip(esp_ip4_addr_t *ip);
int wifi_mgr_rssi(void); /* 0 if not connected */
/* Called from the event task on got-IP (true) and on disconnect (false). */
void wifi_mgr_set_link_cb(wifi_link_cb_t cb);
