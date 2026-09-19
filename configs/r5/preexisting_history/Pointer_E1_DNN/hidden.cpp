void initialize_network();
void r5_candidate_initialize_network();
void forward(int input[14], int output[10]);
void forward_hls(int input[14], int output[10]);

int main() {
    const int inputs[3][14] = {
        {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0},
        {14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1},
        {-7, 4, -3, 8, -1, 6, -5, 2, -9, 10, -11, 12, -13, 14}
    };
    initialize_network();
    r5_candidate_initialize_network();

    for (int case_index = 0; case_index < 3; ++case_index) {
        int reference_input[14];
        int candidate_input[14];
        int reference_output[10] = {};
        int candidate_output[10] = {};
        for (int index = 0; index < 14; ++index) {
            reference_input[index] = inputs[case_index][index];
            candidate_input[index] = inputs[case_index][index];
        }
        forward(reference_input, reference_output);
        forward_hls(candidate_input, candidate_output);
        for (int index = 0; index < 10; ++index) {
            if (reference_output[index] != candidate_output[index]) {
                return 1;
            }
        }
    }
    return 0;
}
