#include <cmath>
#include <cstdio>

#define N 1056768
extern int epsilon[N];
extern void Overlapping(double *result);

int main() {
    for (int i = 0; i < N; ++i) epsilon[i] = (i * 73) % 7 == 0;
    double result = 0.0;
    Overlapping(&result);
    if (std::fabs(result - 2879983.190016) > 1e-6) return 1;
    std::puts("C2HLSC_OVERLAPPING_SOURCE_ORACLE=passed");
    return 0;
}
