/* Per-link window features for the on-board model — the C twin of ml/build_seq.py:link_seq.
 *
 * One link, one window [t0, t0 + WSN_FEAT_WIN_S): frames of that transmitter (amplitude of 64 subcarriers)
 * -> 40 steps of 0.1 s x 4 channels:
 *   0 dev  mean over subcarriers of |bin mean - window mean|
 *   1 jit  mean over subcarriers of the std inside the bin
 *   2 pc1, 3 pc2  projection of the bin mean on the 2 main directions of change (sign: weights sum >= 0)
 * AGC-normalised amplitude of live subcarriers (mean > 2); empty bins linearly filled; x100.
 * A silent link (< 12 frames, < 8 live subcarriers or < 4 filled bins) gives all zeros.
 * Tested against the Python implementation in test/ (host build).
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WSN_FEAT_NT 40
#define WSN_FEAT_NC 4
#define WSN_FEAT_SC 64
#define WSN_FEAT_STEP_S 0.1f
#define WSN_FEAT_WIN_S 4.0f
#define WSN_FEAT_MIN_FRAMES 12

typedef struct {
    float t;                      /* seconds, any origin (same as t0) */
    uint16_t amp[WSN_FEAT_SC];    /* |H_k| * WSN_FEAT_AMP_SCALE, subcarriers in -32..31 order (REORDER in Python) */
} wsn_feat_frame_t;

#define WSN_FEAT_AMP_SCALE 32.0f  /* шаг 1/32: ошибка округления << изменений канала от движения */

/* |H_k| of the 64 HT-LTF (or L-LTF) subcarriers from ESP CSI bytes (int8 imag, real pairs, ESP order). */
void wsn_feat_amplitude(const int8_t *iq128, uint16_t amp[WSN_FEAT_SC]);

/* frames sorted by t. Returns the number of frames used (0 -> out is all zeros).
 * warm: optional per-link state (2 x 64 floats, zero-initialised by the caller): the main axes of the previous
 * window start the iteration and receive the new ones — overlapping windows converge in a few steps. NULL = cold. */
int wsn_feat_link(const wsn_feat_frame_t *frames, int n, float t0, float out[WSN_FEAT_NT][WSN_FEAT_NC],
                  float warm[2][WSN_FEAT_SC]);
/* iterations of the last call (both axes), for profiling */
int wsn_feat_last_iters(void);

/* sign(x) * log1p(|x|) — the input squash of the network */
float wsn_feat_squash(float x);

#ifdef __cplusplus
}
#endif
