/* Model update over the network (OTA of the model blob only, firmware stays).
 *
 * The server sends signed WSN_TYPE_MODEL_CHUNK packets (bound to this boot); chunks are written in order into
 * the inactive model partition (model_a / model_b). When the whole blob is there and its header and CRC check
 * out, the slot is switched in NVS and the board restarts with the new model. A broken or partial upload never
 * touches the active slot. Progress goes back as WSN_TYPE_MODEL_ACK.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "wsn_proto.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t model_ota_start(void);
/* Active slot from NVS: 0 = model_a, 1 = model_b. */
uint8_t model_ota_active_slot(void);
/* Queue a verified chunk for writing (non-blocking; a full queue drops it, the server resends). */
void model_ota_feed(const wsn_model_chunk_t *c);
/* Current progress; returns true when it changed since the last call (worth an ACK). */
bool model_ota_status(wsn_model_ack_t *out);

#ifdef __cplusplus
}
#endif
