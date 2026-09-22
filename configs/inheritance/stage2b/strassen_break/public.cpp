#include <cstdio>

void process_top(int *np, int *lp, int *mp, int *mat1, int *mat2, int *mat3, int *fallback);
void process_top_hls(int *np, int *lp, int *mp, int *mat1, int *mat2, int *mat3, int *fallback);

int main() {
    int n = 2, l = 2, m = 2;
    int source_a[16] = {1, 2, 3, 4};
    int candidate_a[16] = {1, 2, 3, 4};
    int source_b[16] = {5, 6, 7, 8};
    int candidate_b[16] = {5, 6, 7, 8};
    int source_c[16] = {0};
    int candidate_c[16] = {0};
    int source_fallback = 0;
    int candidate_fallback = 0;
    process_top(&n, &l, &m, source_a, source_b, source_c, &source_fallback);
    process_top_hls(&n, &l, &m, candidate_a, candidate_b, candidate_c, &candidate_fallback);
    if (source_fallback != candidate_fallback) return 1;
    for (int i = 0; i < 4; ++i) if (source_c[i] != candidate_c[i]) return 1;
    std::puts("HETEROREFACTOR_STRASSEN_BREAK_PUBLIC_ORACLE=passed");
    return 0;
}
