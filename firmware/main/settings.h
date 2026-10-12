#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#define WSN_FW_VER 1

typedef struct {
    char ssid[33];
    char pass[65];
    uint8_t bssid[6];   /* all zero = not set */
    char host[64];
    uint16_t port;
    uint8_t key[32];    /* all zero = not set */
    uint16_t ping_hz;
    uint8_t csi_source; /* 0 = ht, 1 = lltf */
} wsn_settings_t;

/* Missing fields get defaults: port=5005, ping_hz=100, csi_source=0. */
esp_err_t settings_load(wsn_settings_t *out);
esp_err_t settings_save(const wsn_settings_t *s);
/* Monotonic boot counter kept in NVS: increments and returns the new value (>= 1). 0 on NVS error. */
uint32_t settings_next_boot_id(void);
/* True when ssid, bssid, host and key are all set. */
bool settings_complete(const wsn_settings_t *s);
