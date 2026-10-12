#pragma once
#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "wsn_proto.h"

#define CSI_MAX_LEN 384

typedef struct {
    uint32_t accepted;  /* from BSSID and matching csi_source */
    uint32_t dropped;   /* from BSSID, not matching csi_source (still forwarded in phase 1) */
    uint32_t overflow;  /* ring buffer full: frame lost */
    int64_t rssi_sum;   /* over accepted + dropped */
    uint32_t rssi_n;
} csi_counters_t;

/* Must be called after esp_wifi_start(). csi_source: 0 = ht, 1 = lltf. */
esp_err_t csi_capture_start(const uint8_t bssid[6], uint8_t csi_source);
void csi_capture_counters(csi_counters_t *out);
/* Pop next frame; data copied into data_buf (>= CSI_MAX_LEN), out->data points to it. */
bool csi_capture_pop(wsn_csi_frame_t *out, int8_t *data_buf, TickType_t wait);
