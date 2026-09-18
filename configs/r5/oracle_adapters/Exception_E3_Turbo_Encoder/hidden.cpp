void regUpdate(bool input, bool terminate, bool reg[3], bool output[2]);
void regUpdate_hls(bool input, bool terminate, bool reg[3], bool output[2]);

int main() {
    bool reference_reg[3] = {true, false, true};
    bool candidate_reg[3] = {true, false, true};
    const bool bits[] = {false, true, false, false, true};
    for (int step = 0; step < 5; ++step) {
        bool reference_output[2] = {};
        bool candidate_output[2] = {};
        const bool terminate = step >= 3;
        regUpdate(bits[step], terminate, reference_reg, reference_output);
        regUpdate_hls(bits[step], terminate, candidate_reg, candidate_output);
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
