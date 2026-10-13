#pragma once
#include "esp_err.h"
#include "settings.h"

/* Starts the uplink task: batches CSI frames into signed UDP packets, sends telemetry each second,
 * restarts the ping generator if CSI stalls. Requires settings_complete(). */
esp_err_t uplink_start(const wsn_settings_t *s, uint32_t boot_id);
/* Accepted CSI frames per second over the last telemetry interval. */
float uplink_csi_hz(void);
/* One text line to the server (WSN_TYPE_LOG), also printed to the console. Up to 8 lines wait for the socket;
 * more are dropped. Callable from any task (not from an ISR). */
void uplink_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
