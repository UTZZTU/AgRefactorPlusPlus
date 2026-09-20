#include <cstdio>

#include "fir.h"

int main() {
    int taps[NUM_TAPS] = {3, -2, 5, 1};
    int expected_delay[NUM_TAPS] = {};
    const int inputs[] = {1, 0, -3, 7, 2, -1, 4, 9};
    for (int input : inputs) {
        for (int index = NUM_TAPS - 1; index > 0; --index) {
            expected_delay[index] = expected_delay[index - 1];
        }
        expected_delay[0] = input;
        int expected = 0;
        for (int index = 0; index < NUM_TAPS; ++index) {
            expected += expected_delay[index] * taps[index];
        }
        int actual = 0;
        fir(input, &actual, taps);
        if (actual != expected) {
            std::fprintf(
                stderr,
                "HLSPILOT_FIR_MISMATCH input=%d actual=%d expected=%d\n",
                input,
                actual,
                expected
            );
            return 1;
        }
    }
    std::puts("HLSPILOT_FIR_ORACLE=passed");
    return 0;
}
