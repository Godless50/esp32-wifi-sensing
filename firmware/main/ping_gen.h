#pragma once
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_netif_ip_addr.h"

#define PING_GEN_MAX_TARGETS 8

/* ICMP echo to `gw` at `hz` (1..200). Idempotent: restarts if already running. Thread-safe. */
esp_err_t ping_gen_start(esp_ip4_addr_t gw, uint16_t hz);
void ping_gen_stop(void);
/* Change the rate of a running generator (no restart). */
void ping_gen_set_hz(uint16_t hz);
/* Home devices pinged besides the gateway, `hz` (1..50) each: every reply is a frame of that device on the air,
 * heard by the board in promiscuous mode (a radio link "device -> board"). ips: lwIP order (esp_ip4_addr_t.addr),
 * 0 entries are skipped. n = 0 stops them. Thread-safe; takes effect within one period. */
void ping_gen_set_targets(const uint32_t *ips, size_t n, uint16_t hz);
