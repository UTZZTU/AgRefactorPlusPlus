#include <iostream>

extern "C" void candidate_top(const int input[8], int output[8]);
extern "C" void reference_top(const int input[8], int output[8]);

int main() {
    int input[8] = {-4, -1, 0, 1, 2, 7, 19, 31};
    int expected[8] = {};
    int observed[8] = {};

    reference_top(input, expected);
    candidate_top(input, observed);

    for (int i = 0; i < 8; ++i) {
        if (observed[i] != expected[i]) {
            std::cerr << "mismatch index=" << i
                      << " expected=" << expected[i]
                      << " observed=" << observed[i] << "\n";
            return 1;
        }
    }
    return 0;
}
