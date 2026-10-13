#pragma once
#include <stdint.h>

#include "esp_err.h"
#include "esp_netif_ip_addr.h"

/* ICMP echo to `gw` at `hz` (1..200). Idempotent: restarts if already running. Thread-safe. */
esp_err_t ping_gen_start(esp_ip4_addr_t gw, uint16_t hz);
void ping_gen_stop(void);
/* Change the rate of a running generator (no restart). */
void ping_gen_set_hz(uint16_t hz);
