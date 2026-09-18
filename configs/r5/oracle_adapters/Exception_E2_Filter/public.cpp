#include <cstdint>

using N_TYPE = uint16_t;
using IN_TYPE = float;
using OUT_TYPE = float;

void run(IN_TYPE& fixed_in, OUT_TYPE& fixed_out, N_TYPE& n_sample);
void run_hls(IN_TYPE& fixed_in, OUT_TYPE& fixed_out, N_TYPE& n_sample);

int main() {
    IN_TYPE reference_input = 7.25f;
    IN_TYPE candidate_input = reference_input;
    OUT_TYPE reference_output = 0.0f;
    OUT_TYPE candidate_output = 0.0f;
    N_TYPE reference_count = 0;
    N_TYPE candidate_count = 0;
    run(reference_input, reference_output, reference_count);
    run_hls(candidate_input, candidate_output, candidate_count);
    if (reference_output != candidate_output
        || reference_count != candidate_count
        || reference_input != candidate_input) {
        return 1;
    }
    return 0;
}
