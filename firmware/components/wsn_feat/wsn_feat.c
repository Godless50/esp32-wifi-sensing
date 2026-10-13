/* Per-link window features — see include/wsn_feat.h. Mirrors ml/build_seq.py:link_seq step by step. */
#include "wsn_feat.h"

#include <math.h>
#include <string.h>

#define NT WSN_FEAT_NT
#define SC WSN_FEAT_SC
#define MAX_FRAMES 1024  /* 4 с по 140 Гц у точки доступа — с запасом */

/* рабочие буферы: функция вызывается из одной задачи */
static float s_g[MAX_FRAMES];
static float s_sum[NT][SC], s_sq[NT][SC];
static int s_cnt[NT];
static float s_means[NT][SC], s_jit[NT];
static float s_cov[SC][SC];

float wsn_feat_squash(float x)
{
    return copysignf(log1pf(fabsf(x)), x);
}

void wsn_feat_amplitude(const int8_t *iq, uint16_t amp[SC])
{
    for (int k = 0; k < SC; k++) {
        int src = (k + 32) % 64; /* REORDER = r_[32:64, 0:32] */
        float im = iq[2 * src], re = iq[2 * src + 1];
        float a = sqrtf(im * im + re * re) * WSN_FEAT_AMP_SCALE + 0.5f;
        amp[k] = a > 65535.0f ? 65535 : (uint16_t)a;
    }
}

/* np.interp(x, x[ok], y[ok]) over the 40 bins, in place: y[k*stride] for k without ok[k] */
static void fill_bins(float *y, int stride, const int *ok)
{
    int first = -1, last = -1;
    for (int k = 0; k < NT; k++) {
        if (ok[k]) {
            if (first < 0) first = k;
            last = k;
        }
    }
    for (int k = 0; k < NT; k++) {
        if (ok[k]) continue;
        if (k < first) {
            y[k * stride] = y[first * stride];
        } else if (k > last) {
            y[k * stride] = y[last * stride];
        } else {
            int p = k - 1, q = k + 1;
            while (!ok[p]) p--;
            while (!ok[q]) q++;
            float w = (float)(k - p) / (float)(q - p);
            y[k * stride] = y[p * stride] + (y[q * stride] - y[p * stride]) * w;
        }
    }
}

/* top eigenvector of the symmetric S x S matrix s_cov (power iteration); v gets unit length, returns eigenvalue */
static float top_eig(int S, float *v)
{
    float w[SC];
    float lam = 0;
    for (int it = 0; it < 150; it++) {  /* хост-тест: 150 итераций совпадают с SVD Python до 1e-3 */
        float nrm = 0;
        for (int i = 0; i < S; i++) {
            float acc = 0;
            for (int j = 0; j < S; j++) acc += s_cov[i][j] * v[j];
            w[i] = acc;
            nrm += acc * acc;
        }
        nrm = sqrtf(nrm);
        if (nrm < 1e-20f) return 0;
        float diff = 0;
        for (int i = 0; i < S; i++) {
            float x = w[i] / nrm;
            diff += fabsf(x - v[i]);
            v[i] = x;
        }
        lam = nrm;
        if (diff < 1e-6f * S) break;
    }
    return lam;
}

int wsn_feat_link(const wsn_feat_frame_t *fr, int n, float t0, float out[NT][WSN_FEAT_NC])
{
    memset(out, 0, sizeof(float) * NT * WSN_FEAT_NC);
    const float t1 = t0 + WSN_FEAT_WIN_S;
    int i0 = 0;
    while (i0 < n && fr[i0].t < t0) i0++;
    int i1 = i0;
    while (i1 < n && fr[i1].t < t1) i1++;
    int m = i1 - i0;
    if (m < WSN_FEAT_MIN_FRAMES) return 0;
    if (m > MAX_FRAMES) { i0 = i1 - MAX_FRAMES; m = MAX_FRAMES; }
    const float inv = 1.0f / WSN_FEAT_AMP_SCALE;

    /* живые поднесущие: средняя амплитуда за окно > 2 */
    int live[SC], S = 0;
    for (int k = 0; k < SC; k++) {
        double acc = 0;
        for (int i = i0; i < i1; i++) acc += fr[i].amp[k];
        if (acc * inv / m > 2.0) live[S++] = k;
    }
    if (S < 8) return 0;

    /* АРУ: делим кадр на его средний уровень; затем среднее окна по каждой поднесущей */
    float wm[SC] = {0};
    for (int i = i0; i < i1; i++) {
        float g = 0;
        for (int j = 0; j < S; j++) g += fr[i].amp[live[j]] * inv;
        g = g / S + 1e-3f;
        s_g[i - i0] = g;
        for (int j = 0; j < S; j++) wm[j] += fr[i].amp[live[j]] * inv / g;
    }
    for (int j = 0; j < S; j++) wm[j] /= m;

    /* по корзинам 0.1 с: сумма и сумма квадратов отклонений от среднего окна */
    memset(s_sum, 0, sizeof(s_sum));
    memset(s_sq, 0, sizeof(s_sq));
    memset(s_cnt, 0, sizeof(s_cnt));
    for (int i = i0; i < i1; i++) {
        int b = (int)((fr[i].t - t0) / WSN_FEAT_STEP_S);
        if (b > NT - 1) b = NT - 1;
        if (b < 0) b = 0;
        s_cnt[b]++;
        float g = s_g[i - i0];
        for (int j = 0; j < S; j++) {
            float a = fr[i].amp[live[j]] * inv / g - wm[j];
            s_sum[b][j] += a;
            s_sq[b][j] += a * a;
        }
    }

    int ok[NT], okj[NT], nok = 0, nokj = 0;
    for (int b = 0; b < NT; b++) {
        ok[b] = s_cnt[b] > 0;
        okj[b] = s_cnt[b] > 1;
        nok += ok[b];
        nokj += okj[b];
        s_jit[b] = 0;
        for (int j = 0; j < S; j++) {
            float mu = ok[b] ? s_sum[b][j] / s_cnt[b] : 0;
            s_means[b][j] = mu;
            if (okj[b]) {
                float var = s_sq[b][j] / s_cnt[b] - mu * mu;
                s_jit[b] += sqrtf(var > 0 ? var : 0);
            }
        }
        if (okj[b]) s_jit[b] /= S;
    }
    if (nok < 4) return 0;
    for (int j = 0; j < S; j++) fill_bins(&s_means[0][j], SC, ok);
    if (nokj >= 2) {
        fill_bins(s_jit, 1, okj);
    } else {
        for (int b = 0; b < NT; b++) s_jit[b] = 0;
    }

    /* две главные оси изменения средних по корзинам (как SVD в Python), знак: сумма весов >= 0 */
    float cm[SC] = {0};
    for (int b = 0; b < NT; b++)
        for (int j = 0; j < S; j++) cm[j] += s_means[b][j];
    for (int j = 0; j < S; j++) cm[j] /= NT;
    for (int i = 0; i < S; i++) {
        for (int j = i; j < S; j++) {
            float acc = 0;
            for (int b = 0; b < NT; b++) acc += (s_means[b][i] - cm[i]) * (s_means[b][j] - cm[j]);
            s_cov[i][j] = s_cov[j][i] = acc;
        }
    }
    float v[2][SC];
    for (int c = 0; c < 2; c++) {
        for (int j = 0; j < S; j++) v[c][j] = (c == 0 ? 1.0f : (j & 1 ? -1.0f : 1.0f)) + 0.01f * (float)j;
        float lam = top_eig(S, v[c]);
        float sum = 0;
        for (int j = 0; j < S; j++) sum += v[c][j];
        if (sum < 0)
            for (int j = 0; j < S; j++) v[c][j] = -v[c][j];
        for (int i = 0; i < S; i++)  /* дефляция: убираем найденную ось */
            for (int j = 0; j < S; j++) s_cov[i][j] -= lam * v[c][i] * v[c][j];
    }

    for (int b = 0; b < NT; b++) {
        float dev = 0, p1 = 0, p2 = 0;
        for (int j = 0; j < S; j++) {
            dev += fabsf(s_means[b][j]);
            p1 += s_means[b][j] * v[0][j];
            p2 += s_means[b][j] * v[1][j];
        }
        out[b][0] = 100.0f * dev / S;
        out[b][1] = 100.0f * s_jit[b];
        out[b][2] = 100.0f * p1;
        out[b][3] = 100.0f * p2;
    }
    return m;
}
