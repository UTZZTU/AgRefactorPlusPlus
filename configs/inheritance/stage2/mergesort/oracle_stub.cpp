#include <algorithm>

void process_top_hls(int n, int *input, int *output, int *fallback) {
    std::copy(input, input + n, output);
    std::sort(output, output + n);
    *fallback = 0;
}
