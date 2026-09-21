void process_top_hls(
    int *np,
    int *lp,
    int *mp,
    int *mat1,
    int *mat2,
    int *mat3,
    int *fallback
) {
    for (int i = 0; i < *np; ++i) {
        for (int j = 0; j < *mp; ++j) {
            int value = 0;
            for (int k = 0; k < *lp; ++k) {
                value += mat1[i * *lp + k] * mat2[k * *mp + j];
            }
            mat3[i * *mp + j] = value;
        }
    }
    *fallback = 0;
}
