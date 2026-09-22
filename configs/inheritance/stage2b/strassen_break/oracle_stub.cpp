#include <cstdlib>

void process_top_hls(int *np, int *lp, int *mp, int *mat1, int *mat2, int *mat3, int *fallback) {
    int n = *np, l = *lp, m = *mp;
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j < m; ++j) {
            int value = 0;
            for (int k = 0; k < l; ++k) value += mat1[i * l + k] * mat2[k * m + j];
            mat3[i * m + j] = value;
        }
    }
    *fallback = 0;
}
