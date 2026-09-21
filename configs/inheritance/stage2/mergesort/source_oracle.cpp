#include <cstdio>

void process_top(int n, int *input, int *output, int *fallback);

int main() {
    const int n = 7;
    int input[32] = {12, 3, 9, -1, 5, 5, 0};
    int output[32];
    for (int i = 0; i < 32; ++i) output[i] = 0x24681357;
    int fallback = 0;
    process_top(n, input, output, &fallback);
    const int expected[7] = {-1, 0, 3, 5, 5, 9, 12};
    if (fallback != 0) return 1;
    for (int i = 0; i < n; ++i) {
        if (output[i] != expected[i]) return 1;
    }
    std::puts("HETEROREFACTOR_MERGESORT_SOURCE_ORACLE=passed");
    return 0;
}
