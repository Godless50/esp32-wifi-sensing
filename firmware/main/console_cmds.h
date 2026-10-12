#pragma once
#include "esp_err.h"
#include "settings.h"

typedef float (*csi_hz_fn_t)(void);

/* Start the USB console REPL. `active` = settings the firmware is running with (shown in status). */
esp_err_t console_start(const wsn_settings_t *active, csi_hz_fn_t csi_hz);
