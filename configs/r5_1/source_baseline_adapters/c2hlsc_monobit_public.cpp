#include <cstdio>

#define N 128
extern int epsilon[N];
void Frequency(int *result);

int main() {
    for (int i = 0; i < N; ++i) {
        epsilon[i] = (i * 73) % 7 == 0;
    }
    int result = 0;
    Frequency(&result);
    if (result != -90) {
        return 1;
    }
    std::puts("C2HLSC_MONOBIT_SOURCE_ORACLE=passed");
    return 0;
}
