#include <cstring>

constexpr int MAX_SIZE = 8;

void process_top(int n, int input[MAX_SIZE], int output[MAX_SIZE], int* fallback);
extern "C" void process_top_hls(
    int n,
    int input[MAX_SIZE],
    int output[MAX_SIZE],
    int* fallback
);

int main() {
    int reference_input[MAX_SIZE] = {4, 2, 6, 1, 3, 5, 7, 0};
    int candidate_input[MAX_SIZE] = {};
    std::memcpy(candidate_input, reference_input, sizeof(reference_input));
    int reference_output[MAX_SIZE] = {};
    int candidate_output[MAX_SIZE] = {};
    int reference_fallback = 0;
    int candidate_fallback = 0;
    process_top(7, reference_input, reference_output, &reference_fallback);
    process_top_hls(7, candidate_input, candidate_output, &candidate_fallback);
    if (reference_fallback != candidate_fallback) {
        return 1;
    }
    for (int index = 0; index < 7; ++index) {
        if (reference_output[index] != candidate_output[index]) {
            return 1;
        }
    }
    return 0;
}
