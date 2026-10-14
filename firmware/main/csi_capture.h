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
    uint32_t links;     /* frames from extra transmitters */
    int64_t rssi_sum;   /* over accepted + dropped */
    uint32_t rssi_n;
} csi_counters_t;

#define CSI_SRC_MAX 24

typedef struct {
    uint8_t mac[6];
    uint32_t frames, ht;
    int32_t rssi_sum;
} csi_src_stat_t;

/* Per-transmitter CSI statistics since last reset (diagnostics for extra links). */
size_t csi_capture_sources(csi_src_stat_t *out, size_t cap, bool reset);

/* Must be called after esp_wifi_start(). csi_source: 0 = ht, 1 = lltf.
 * links: extra transmitter MACs whose CSI is forwarded too (enables promiscuous mode when n_links > 0). */
esp_err_t csi_capture_start(const uint8_t bssid[6], uint8_t csi_source, const uint8_t (*links)[6], size_t n_links,
                            bool promisc);
void csi_capture_counters(csi_counters_t *out);
/* Add a transmitter (another board heard over ESP-NOW) to the captured links; false if known, full or not started. */
bool csi_capture_add_link(const uint8_t mac[6]);
/* Pop next frame; data copied into data_buf (>= CSI_MAX_LEN), out->data points to it. */
bool csi_capture_pop(wsn_csi_frame_t *out, int8_t *data_buf, TickType_t wait);
/* Stop / restart CSI and the Wi-Fi sniffer around flash writes: a flash write while the sniffer copies a frame
 * crashed the boards ("Cache disabled but cached memory region accessed" in wDev_SnifferRxData). */
void csi_capture_pause(void);
void csi_capture_resume(void);
