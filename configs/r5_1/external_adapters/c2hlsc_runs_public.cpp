#include <cstdio>

extern int epsilon[65535];
void Runs(int *res_s, int *res_v);

int main() {
    for (int pattern = 0; pattern < 3; ++pattern) {
        int expected_s = 0;
        int expected_v = 1;
        for (int index = 0; index < 65535; ++index) {
            int value = 0;
            if (pattern == 0) {
                value = (index % 2 == 0);
            } else if (pattern == 1) {
                value = (index % 7 == 0);
            } else {
                value = (index >= 123 && index < 49152);
            }
            epsilon[index] = value;
            expected_s += value;
            if (index > 0 && epsilon[index] != epsilon[index - 1]) {
                ++expected_v;
            }
        }
        int actual_s = -1;
        int actual_v = -1;
        Runs(&actual_s, &actual_v);
        if (actual_s != expected_s || actual_v != expected_v) {
            std::fprintf(
                stderr,
                "C2HLSC_RUNS_MISMATCH pattern=%d actual=(%d,%d) expected=(%d,%d)\n",
                pattern,
                actual_s,
                actual_v,
                expected_s,
                expected_v
            );
            return 1;
        }
    }
    std::puts("C2HLSC_RUNS_ORACLE=passed");
    return 0;
}
