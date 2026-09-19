void initialize_network();
void r5_candidate_initialize_network();
void forward(int input[14], int output[10]);
void forward_hls(int input[14], int output[10]);

int main() {
    int reference_input[14] = {
        1, -2, 3, -4, 5, -6, 7, -8, 9, -10, 11, -12, 13, -14
    };
    int candidate_input[14];
    int reference_output[10] = {};
    int candidate_output[10] = {};
    for (int index = 0; index < 14; ++index) {
        candidate_input[index] = reference_input[index];
    }

    initialize_network();
    r5_candidate_initialize_network();
    forward(reference_input, reference_output);
    forward_hls(candidate_input, candidate_output);

    for (int index = 0; index < 10; ++index) {
        if (reference_output[index] != candidate_output[index]) {
            return 1;
        }
    }
    return 0;
}
