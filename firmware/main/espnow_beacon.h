#pragma once

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Start broadcasting small ESP-NOW frames at hz (after Wi-Fi is connected; idempotent; 0 = off). */
esp_err_t espnow_beacon_start(uint16_t hz);

#ifdef __cplusplus
}
#endif
