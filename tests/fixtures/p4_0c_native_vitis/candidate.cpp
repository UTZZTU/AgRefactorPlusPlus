extern "C" void candidate_top(const int input[8], int output[8]) {
#pragma HLS INTERFACE m_axi port=input offset=slave bundle=gmem0
#pragma HLS INTERFACE m_axi port=output offset=slave bundle=gmem1
#pragma HLS INTERFACE s_axilite port=input bundle=control
#pragma HLS INTERFACE s_axilite port=output bundle=control
#pragma HLS INTERFACE s_axilite port=return bundle=control
    for (int i = 0; i < 8; ++i) {
#pragma HLS PIPELINE II=1
        output[i] = input[i] * 3 + 1;
    }
}
