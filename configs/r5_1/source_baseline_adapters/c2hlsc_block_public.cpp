#include <cmath>
#include <cstdio>

#define N 16
#define M 8
extern int epsilon[N * M];
void BlockFrequency(double *result);

int main() {
    for (int i = 0; i < N * M; ++i) {
        epsilon[i] = (i * 73) % 7 == 0;
    }
    double result = 0.0;
    BlockFrequency(&result);
    if (std::fabs(result - 2.015625) > 1e-9) {
        return 1;
    }
    std::puts("C2HLSC_BLOCK_SOURCE_ORACLE=passed");
    return 0;
}
