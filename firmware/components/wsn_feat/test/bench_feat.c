#define _POSIX_C_SOURCE 199309L
/* Host profile of wsn_feat_link: time per call by window size (vectors.bin), to see what to speed up on the S3. */
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "wsn_feat.h"

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

int main(int argc, char **argv)
{
    FILE *f = fopen(argc > 1 ? argv[1] : "vectors.bin", "rb");
    if (!f) return 2;
    uint32_t nc;
    if (fread(&nc, 4, 1, f) != 1) return 2;
    static wsn_feat_frame_t fr[4096];
    float out[WSN_FEAT_NT][WSN_FEAT_NC], exp[WSN_FEAT_NT][WSN_FEAT_NC];
    double big = 0, small = 0; int nb = 0, ns = 0, iters_b = 0, iters_s = 0;
    for (uint32_t c = 0; c < nc; c++) {
        uint32_t n; float t0;
        if (fread(&n, 4, 1, f) != 1 || fread(&t0, 4, 1, f) != 1) return 2;
        for (uint32_t i = 0; i < n; i++)
            if (fread(&fr[i].t, 4, 1, f) != 1 || fread(fr[i].amp, sizeof(fr[i].amp), 1, f) != 1) return 2;
        if (fread(exp, sizeof(exp), 1, f) != 1) return 2;
        double t = now_s();
        for (int r = 0; r < 200; r++) wsn_feat_link(fr, (int)n, t0, out, NULL);
        double dt = (now_s() - t) / 200;
        if (n > 300) { big += dt; nb++; iters_b += wsn_feat_last_iters(); } else { small += dt; ns++; iters_s += wsn_feat_last_iters(); }
    }
    printf("host: %.1f us per AP-size window (%d, %.0f iters), %.1f us per 20 Hz link window (%d, %.0f iters)\n",
           1e6 * big / (nb ? nb : 1), nb, (double)iters_b / (nb ? nb : 1), 1e6 * small / (ns ? ns : 1), ns,
           (double)iters_s / (ns ? ns : 1));
    return 0;
}
