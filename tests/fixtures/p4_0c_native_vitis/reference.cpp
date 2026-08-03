extern "C" void reference_top(const int input[8], int output[8]) {
    for (int i = 0; i < 8; ++i) {
        output[i] = input[i] * 3 + 1;
    }
}
