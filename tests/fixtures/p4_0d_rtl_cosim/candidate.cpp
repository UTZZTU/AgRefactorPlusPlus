extern "C" void vector_add(const int a[4], const int b[4], int c[4]) {
#pragma HLS INTERFACE ap_memory port=a
#pragma HLS INTERFACE ap_memory port=b
#pragma HLS INTERFACE ap_memory port=c
    for (int i = 0; i < 4; ++i) c[i] = a[i] + b[i];
}
