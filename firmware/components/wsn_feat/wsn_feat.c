/* Per-link window features — see include/wsn_feat.h. Mirrors ml/build_seq.py:link_seq step by step. */
#include "wsn_feat.h"

#include <math.h>
#include <string.h>

#define NT WSN_FEAT_NT
#define SC WSN_FEAT_SC
#define MAX_FRAMES 1024  /* 4 с по 140 Гц у точки доступа — с запасом */

/* рабочие буферы: функция вызывается из одной задачи */
static float s_sum[NT][SC], s_sq[NT][SC];
static int s_cnt[NT];
static float s_means[NT][SC], s_jit[NT];
static float s_gram[NT][SC];  /* M M^T по корзинам (40 x 40), строки с шагом SC */
static int s_iters;

int wsn_feat_last_iters(void) { return s_iters; }

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

/* top eigenvector of the symmetric n x n matrix A (row stride SC) by power iteration; v: unit length on return */
static float top_eig(const float A[][SC], int n, float *v)
{
    float w[SC];
    float lam = 0;
    for (int it = 0; it < 150; it++) {  /* хост-тест: совпадает с SVD Python до 2e-3 */
        s_iters++;
        float nrm = 0;
        for (int i = 0; i < n; i++) {
            const float *row = A[i];
            float acc = 0;
            for (int j = 0; j < n; j++) acc += row[j] * v[j];
            w[i] = acc;
            nrm += acc * acc;
        }
        nrm = sqrtf(nrm);
        if (nrm < 1e-20f) return 0;
        const float r = 1.0f / nrm;
        float diff = 0;
        for (int i = 0; i < n; i++) {
            float x = w[i] * r;
            diff += fabsf(x - v[i]);
            v[i] = x;
        }
        lam = nrm;
        if (diff < 1e-6f * n) break;
    }
    return lam;
}

int wsn_feat_link(const wsn_feat_frame_t *fr, int n, float t0, float out[NT][WSN_FEAT_NC], float warm[2][SC])
{
    s_iters = 0;
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

    /* живые поднесущие: средняя амплитуда за окно > 2 (целые суммы: на S3 нет аппаратного double) */
    int live[SC], S = 0;
    const uint32_t thr = (uint32_t)(2.0f * WSN_FEAT_AMP_SCALE) * (uint32_t)m;
    for (int k = 0; k < SC; k++) {
        uint32_t acc = 0;
        for (int i = i0; i < i1; i++) acc += fr[i].amp[k];
        if (acc > thr) live[S++] = k;
    }
    if (S < 8) return 0;

    /* один проход: АРУ (делим кадр на его средний уровень) и суммы по корзинам 0.1 с.
     * Суммы считаем от сдвига K_j (первый кадр) — без потери точности на малых дисперсиях. */
    float K[SC], tot[SC];
    memset(s_sum, 0, sizeof(s_sum));
    memset(s_sq, 0, sizeof(s_sq));
    memset(s_cnt, 0, sizeof(s_cnt));
    memset(tot, 0, sizeof(tot));
    for (int i = i0; i < i1; i++) {
        float g = 0;
        for (int j = 0; j < S; j++) g += fr[i].amp[live[j]];
        const float sc = 1.0f / (g * inv / S + 1e-3f) * inv;  /* amp * sc = нормированная амплитуда */
        if (i == i0)
            for (int j = 0; j < S; j++) K[j] = fr[i].amp[live[j]] * sc;
        int b = (int)((fr[i].t - t0) * (1.0f / WSN_FEAT_STEP_S));
        if (b > NT - 1) b = NT - 1;
        if (b < 0) b = 0;
        s_cnt[b]++;
        float *su = s_sum[b], *sq = s_sq[b];
        for (int j = 0; j < S; j++) {
            float a = fr[i].amp[live[j]] * sc - K[j];
            su[j] += a;
            sq[j] += a * a;
            tot[j] += a;
        }
    }
    float wm[SC];  /* среднее окна (от сдвига K) */
    for (int j = 0; j < S; j++) wm[j] = tot[j] / m;

    int ok[NT], okj[NT], nok = 0, nokj = 0;
    for (int b = 0; b < NT; b++) {
        ok[b] = s_cnt[b] > 0;
        okj[b] = s_cnt[b] > 1;
        nok += ok[b];
        nokj += okj[b];
        s_jit[b] = 0;
        const float rn = ok[b] ? 1.0f / s_cnt[b] : 0;
        for (int j = 0; j < S; j++) {
            float mk = s_sum[b][j] * rn;           /* среднее корзины от сдвига K */
            s_means[b][j] = ok[b] ? mk - wm[j] : 0; /* отклонение от среднего окна */
            if (okj[b]) {
                float var = s_sq[b][j] * rn - mk * mk;
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

    /* две главные оси изменения средних по корзинам (как SVD в Python), знак: сумма весов >= 0.
     * Через матрицу Грама 40 x 40 (корзины) вместо 56 x 56 (поднесущие): v = M^T u / |M^T u|. */
    float cm[SC] = {0};
    for (int b = 0; b < NT; b++)
        for (int j = 0; j < S; j++) cm[j] += s_means[b][j];
    for (int j = 0; j < S; j++) cm[j] *= 1.0f / NT;
    static float M[NT][SC];
    for (int b = 0; b < NT; b++)
        for (int j = 0; j < S; j++) M[b][j] = s_means[b][j] - cm[j];
    for (int a = 0; a < NT; a++)
        for (int c = a; c < NT; c++) {
            float acc = 0;
            for (int j = 0; j < S; j++) acc += M[a][j] * M[c][j];
            s_gram[a][c] = s_gram[c][a] = acc;
        }
    float v[2][SC];
    for (int c = 0; c < 2; c++) {
        float u[SC], wn = 0;
        if (warm)
            for (int j = 0; j < S; j++) wn += warm[c][live[j]] * warm[c][live[j]];
        for (int b = 0; b < NT; b++) {  /* старт: проекция на ось прошлого окна, иначе фиксированный вектор */
            float acc = 0;
            if (wn > 0.5f)
                for (int j = 0; j < S; j++) acc += M[b][j] * warm[c][live[j]];
            u[b] = wn > 0.5f && acc != 0 ? acc : (c == 0 ? 1.0f : (b & 1 ? -1.0f : 1.0f)) + 0.01f * (float)b;
        }
        float lam = top_eig(s_gram, NT, u);
        float nrm = 0, sum = 0;
        for (int j = 0; j < S; j++) {
            float acc = 0;
            for (int b = 0; b < NT; b++) acc += M[b][j] * u[b];
            v[c][j] = acc;
            nrm += acc * acc;
        }
        nrm = nrm > 1e-30f ? 1.0f / sqrtf(nrm) : 0;
        for (int j = 0; j < S; j++) { v[c][j] *= nrm; sum += v[c][j]; }
        if (sum < 0)
            for (int j = 0; j < S; j++) v[c][j] = -v[c][j];
        for (int a = 0; a < NT; a++)  /* дефляция: убираем найденную ось */
            for (int b = 0; b < NT; b++) s_gram[a][b] -= lam * u[a] * u[b];
    }

    if (warm) {
        memset(warm, 0, sizeof(float) * 2 * SC);
        for (int c = 0; c < 2; c++)
            for (int j = 0; j < S; j++) warm[c][live[j]] = v[c][j];
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
