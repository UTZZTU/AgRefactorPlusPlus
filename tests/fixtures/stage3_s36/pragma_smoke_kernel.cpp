#include <stdint.h>

extern "C" void s36_pragma_top(const int input[64], int output[64]) {
    for (int i = 0; i < 64; ++i) {
        output[i] = input[i] * 3 + 1;
    }
}
