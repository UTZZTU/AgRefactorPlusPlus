#include <stdint.h>

void s35_bottleneck_top(const int32_t input[64], int32_t output[64]) {
    int32_t accumulator = 0;
    bottleneck_loop:
    for (int i = 0; i < 64; ++i) {
        accumulator += input[i];
        output[i] = accumulator;
    }
}
