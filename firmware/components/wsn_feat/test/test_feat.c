/* Host test: C features vs Python link_seq on real windows (vectors.bin from ml/gen_feat_vectors.py). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wsn_feat.h"

static int rd(FILE *f, void *p, size_t n) { return fread(p, 1, n, f) == n; }

int main(int argc, char **argv)
{
    FILE *f = fopen(argc > 1 ? argv[1] : "vectors.bin", "rb");
    if (!f) { perror("vectors.bin"); return 2; }
    uint32_t nc;
    if (!rd(f, &nc, 4)) return 2;
    static wsn_feat_frame_t fr[4096];
    int bad_basic = 0, bad_pc = 0, zero_mismatch = 0;
    double worst_basic = 0, worst_pc = 0, warm_err = 0;
    long iters_warm = 0, iters_cold = 0;
    for (uint32_t c = 0; c < nc; c++) {
        uint32_t n; float t0;
        if (!rd(f, &n, 4) || !rd(f, &t0, 4) || n > 4096) return 2;
        for (uint32_t i = 0; i < n; i++)
            if (!rd(f, &fr[i].t, 4) || !rd(f, fr[i].amp, sizeof(fr[i].amp))) return 2;
        float exp[WSN_FEAT_NT][WSN_FEAT_NC], got[WSN_FEAT_NT][WSN_FEAT_NC];
        if (!rd(f, exp, sizeof(exp))) return 2;
        wsn_feat_link(fr, (int)n, t0, got, NULL);
        /* тёплый старт: окно на 0.25 с раньше даёт оси, потом то же окно — результат тот же, итераций меньше */
        static float warm[2][WSN_FEAT_SC];
        memset(warm, 0, sizeof(warm));
        float tmp[WSN_FEAT_NT][WSN_FEAT_NC], warm_out[WSN_FEAT_NT][WSN_FEAT_NC];
        wsn_feat_link(fr, (int)n, t0 - 0.25f, tmp, warm);
        wsn_feat_link(fr, (int)n, t0, warm_out, warm);
        iters_warm += wsn_feat_last_iters();
        wsn_feat_link(fr, (int)n, t0, tmp, NULL);
        iters_cold += wsn_feat_last_iters();
        for (int k = 0; k < WSN_FEAT_NT; k++)
            for (int ch = 0; ch < WSN_FEAT_NC; ch++)
                warm_err = fmax(warm_err, fabs(warm_out[k][ch] - got[k][ch]) / (fabs(got[k][ch]) + 1.0));
        double scale[WSN_FEAT_NC] = {0}, err[WSN_FEAT_NC] = {0};
        for (int k = 0; k < WSN_FEAT_NT; k++)
            for (int ch = 0; ch < WSN_FEAT_NC; ch++) {
                scale[ch] = fmax(scale[ch], fabs(exp[k][ch]));
                err[ch] = fmax(err[ch], fabs(exp[k][ch] - got[k][ch]));
            }
        int ez = scale[0] == 0, gz = got[0][0] == 0 && got[5][0] == 0 && got[20][0] == 0;
        if (ez != gz) { zero_mismatch++; continue; }
        if (ez) continue;
        for (int ch = 0; ch < 2; ch++) {
            double r = err[ch] / (scale[ch] + 1e-6);
            if (r > worst_basic) worst_basic = r;
            if (r > 1e-3) bad_basic++;
        }
        for (int ch = 2; ch < 4; ch++) {  /* главные оси: при близких собственных числах допустим расхождение */
            double r = err[ch] / (scale[ch] + 1e-6);
            if (r > worst_pc) worst_pc = r;
            if (r > 2e-2) bad_pc++;
        }
    }
    fclose(f);
    printf("cases %u: zero-mismatch %d, dev/jit off %d (worst rel %.2e), pc off %d of %u (worst rel %.2e)\n",
           nc, zero_mismatch, bad_basic, worst_basic, bad_pc, 2 * nc, worst_pc);
    printf("warm start: %.1f iterations per window vs %.1f cold, max deviation from cold %.2e\n",
           (double)iters_warm / nc, (double)iters_cold / nc, warm_err);
    int ok = zero_mismatch == 0 && bad_basic == 0 && bad_pc <= (int)(nc / 10) && warm_err < 0.05;
    printf(ok ? "PASS\n" : "FAIL\n");
    return ok ? 0 : 1;
}
