#include <algorithm>

void process_top_hls(int n, int *input, int *output, int *fallback) {
    int values[32];
    for (int i = 0; i < n; ++i) values[i] = input[n - 1 - i];
    int cursor = 0;
    for (int i = 0; i < n; ++i) output[cursor++] = values[i];
    output[cursor++] = -1;

    for (int i = n; i > 4; --i) values[i] = values[i - 1];
    values[4] = 2000;
    ++n;
    for (int i = 0; i < n; ++i) output[cursor++] = values[i];
    output[cursor++] = -1;

    for (int i = 3; i < n - 1; ++i) values[i] = values[i + 1];
    --n;
    std::sort(values, values + n);
    for (int i = 0; i < n; ++i) output[cursor++] = values[i];
    output[cursor++] = -1;

    for (int i = 0; i < n; ++i) output[cursor++] = values[n - 1 - i];
    output[cursor++] = -1;
    *fallback = 0;
}
