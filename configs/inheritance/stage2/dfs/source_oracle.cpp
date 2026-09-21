#include <cstdio>

void process_top(int n, int *input, int *output, int *fallback);

int main() {
    const int n = 6;
    int input[32] = {5, 2, 8, 1, 3, 7};
    int output[32];
    for (int i = 0; i < 32; ++i) output[i] = 0x5a5a5a5a;
    int fallback = 0;
    process_top(n, input, output, &fallback);
    const int expected[6] = {1, 2, 3, 5, 7, 8};
    if (fallback != 0) return 1;
    for (int i = 0; i < n; ++i) {
        if (output[i] != expected[i]) return 1;
    }
    std::puts("HETEROREFACTOR_DFS_SOURCE_ORACLE=passed");
    return 0;
}
