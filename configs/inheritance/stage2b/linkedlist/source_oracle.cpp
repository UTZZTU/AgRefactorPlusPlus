#include <cstdio>

void process_top(int n, int *input, int *output, int *fallback);

int main() {
    const int n = 6;
    int input[32] = {5, 2, 8, 1, 3, 7};
    int output[32];
    for (int &value : output) value = 0x5a5a5a5a;
    int fallback = 0;
    process_top(n, input, output, &fallback);
    const int expected[] = {
        7, 3, 1, 8, 2, 5, -1,
        7, 3, 1, 8, 2000, 2, 5, -1,
        1, 2, 3, 5, 7, 2000, -1,
        2000, 7, 5, 3, 2, 1, -1,
    };
    if (fallback != 0) return 1;
    for (unsigned i = 0; i < sizeof(expected) / sizeof(expected[0]); ++i) {
        if (output[i] != expected[i]) return 1;
    }
    std::puts("HETEROREFACTOR_LINKEDLIST_SOURCE_ORACLE=passed");
    return 0;
}
