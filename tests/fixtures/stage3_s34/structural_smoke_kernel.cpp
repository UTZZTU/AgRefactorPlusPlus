#include <stdint.h>

void s34_structural_top(const int *input, int *output, int n) {
    for (int i = 0; i < n; ++i) {
        int value = input[i];
        output[i] = value * value + 3 * value + 7;
    }
}
