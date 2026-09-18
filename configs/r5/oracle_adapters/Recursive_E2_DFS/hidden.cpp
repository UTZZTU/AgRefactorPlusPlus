#include <cstring>

constexpr int MAX_SIZE = 8;

void process_top(int n, int input[MAX_SIZE], int output[MAX_SIZE], int* fallback);
void process_top_hls(int n, int input[MAX_SIZE], int output[MAX_SIZE], int* fallback);

int main() {
    const int lengths[] = {1, 4, 8};
    const int inputs[][MAX_SIZE] = {
        {11, 0, 0, 0, 0, 0, 0, 0},
        {10, 20, 5, 15, 0, 0, 0, 0},
        {8, 7, 6, 5, 4, 3, 2, 1},
    };
    for (int case_index = 0; case_index < 3; ++case_index) {
        int reference_input[MAX_SIZE] = {};
        int candidate_input[MAX_SIZE] = {};
        std::memcpy(reference_input, inputs[case_index], sizeof(reference_input));
        std::memcpy(candidate_input, inputs[case_index], sizeof(candidate_input));
        int reference_output[MAX_SIZE] = {};
        int candidate_output[MAX_SIZE] = {};
        int reference_fallback = 0;
        int candidate_fallback = 0;
        process_top(
            lengths[case_index],
            reference_input,
            reference_output,
            &reference_fallback
        );
        process_top_hls(
            lengths[case_index],
            candidate_input,
            candidate_output,
            &candidate_fallback
        );
        if (reference_fallback != candidate_fallback) {
            return 1;
        }
        for (int index = 0; index < lengths[case_index]; ++index) {
            if (reference_output[index] != candidate_output[index]) {
                return 1;
            }
        }
    }
    return 0;
}
