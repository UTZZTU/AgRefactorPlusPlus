#include <cstdio>
#include <cstring>

void process_top(
    int *np,
    int *lp,
    int *mp,
    int *mat1,
    int *mat2,
    int *mat3,
    int *fallback
);
void process_top_hls(
    int *np,
    int *lp,
    int *mp,
    int *mat1,
    int *mat2,
    int *mat3,
    int *fallback
);

int main() {
    int source_n = 2;
    int source_l = 2;
    int source_m = 2;
    int candidate_n = source_n;
    int candidate_l = source_l;
    int candidate_m = source_m;
    int source_mat1[16] = {1, 2, 3, 4};
    int source_mat2[16] = {5, 6, 7, 8};
    int candidate_mat1[16];
    int candidate_mat2[16];
    std::memcpy(candidate_mat1, source_mat1, sizeof(source_mat1));
    std::memcpy(candidate_mat2, source_mat2, sizeof(source_mat2));
    int source_mat3[16];
    int candidate_mat3[16];
    for (int i = 0; i < 16; ++i) {
        source_mat3[i] = candidate_mat3[i] = 0x33333333;
    }
    int source_fallback = 0;
    int candidate_fallback = 0;
    process_top(
        &source_n,
        &source_l,
        &source_m,
        source_mat1,
        source_mat2,
        source_mat3,
        &source_fallback
    );
    process_top_hls(
        &candidate_n,
        &candidate_l,
        &candidate_m,
        candidate_mat1,
        candidate_mat2,
        candidate_mat3,
        &candidate_fallback
    );
    if (source_fallback != 0 || candidate_fallback != source_fallback) return 1;
    for (int i = 0; i < 16; ++i) {
        if (source_mat3[i] != candidate_mat3[i]) return 1;
    }
    std::puts("HETEROREFACTOR_STRASSEN_PUBLIC_ORACLE=passed");
    return 0;
}
