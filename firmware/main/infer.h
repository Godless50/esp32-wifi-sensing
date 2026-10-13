/* On-board model: per-link CSI features (wsn_feat) -> int8 network (TensorFlow Lite Micro + esp-nn, the S3
 * vector instructions) -> zone / activity / "somebody moves", once per second.
 * The model blob lives in the model_a flash partition: 128-byte header ('WSNM', version, link MACs, output
 * order) + .tflite (ml/export_tflite.py). No valid model -> inference stays off, CSI streaming is unaffected.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "wsn_proto.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Load the model and start the inference task (core 0, low priority). */
esp_err_t infer_start(void);
/* Feed one CSI frame (called by the uplink task for every frame it forwards). Cheap, never blocks long. */
void infer_push(const wsn_csi_frame_t *f);
/* Latest answer not yet sent; true once per new result. */
bool infer_take(wsn_pred_t *out);
/* Version of the loaded model (0 = none). */
uint32_t infer_model_ver(void);

#ifdef __cplusplus
}
#endif
