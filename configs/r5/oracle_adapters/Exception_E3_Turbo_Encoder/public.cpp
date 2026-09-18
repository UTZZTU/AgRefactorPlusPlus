void regUpdate(bool input, bool terminate, bool reg[3], bool output[2]);
void regUpdate_hls(bool input, bool terminate, bool reg[3], bool output[2]);

int main() {
    bool reference_reg[3] = {false, false, false};
    bool candidate_reg[3] = {false, false, false};
    const bool bits[] = {true, false, true, true};
    for (bool bit : bits) {
        bool reference_output[2] = {};
        bool candidate_output[2] = {};
        regUpdate(bit, false, reference_reg, reference_output);
        regUpdate_hls(bit, false, candidate_reg, candidate_output);
        for (int index = 0; index < 3; ++index) {
            if (reference_reg[index] != candidate_reg[index]) {
                return 1;
            }
        }
        if (reference_output[0] != candidate_output[0]
            || reference_output[1] != candidate_output[1]) {
            return 1;
        }
    }
    return 0;
}
