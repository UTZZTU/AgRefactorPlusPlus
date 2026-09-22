#include <cstdlib>

void process_top_hls(int n, int *input, int *output, int *fallback) {
    int values[32];
    for (int i = 0; i < n; ++i) values[i] = input[i];
    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            if (values[j] < values[i]) {
                int temp = values[i];
                values[i] = values[j];
                values[j] = temp;
            }
        }
    }
    for (int i = 0; i < n; ++i) output[i] = values[i];
    *fallback = 0;
}
