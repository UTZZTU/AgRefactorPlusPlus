#include <cstdio>

void process_top(
    int *np,
    int *lp,
    int *mp,
    int *mat1,
    int *mat2,
    int *mat3,
    int *fallback
);

int main() {
    int n = 2;
    int l = 2;
    int m = 2;
    int mat1[16] = {1, 2, 3, 4};
    int mat2[16] = {5, 6, 7, 8};
    int mat3[16];
    for (int i = 0; i < 16; ++i) mat3[i] = 0x33333333;
    int fallback = 0;
    process_top(&n, &l, &m, mat1, mat2, mat3, &fallback);
    const int expected[4] = {19, 22, 43, 50};
    if (fallback != 0) return 1;
    for (int i = 0; i < 4; ++i) {
        if (mat3[i] != expected[i]) return 1;
    }
    std::puts("HETEROREFACTOR_STRASSEN_SOURCE_ORACLE=passed");
    return 0;
}
