#include <cstdio>
#include <cstring>

void process_top(int n, int *input, int *output, int *fallback);
void process_top_hls(int n, int *input, int *output, int *fallback);

int main() {
    const int n = 6;
    int source_input[32] = {5, 2, 8, 1, 3, 7};
    int candidate_input[32];
    std::memcpy(candidate_input, source_input, sizeof(source_input));
    int source_output[32];
    int candidate_output[32];
    for (int i = 0; i < 32; ++i) {
        source_output[i] = candidate_output[i] = 0x5a5a5a5a;
    }
    int source_fallback = 0;
    int candidate_fallback = 0;
    process_top(n, source_input, source_output, &source_fallback);
    process_top_hls(n, candidate_input, candidate_output, &candidate_fallback);
    if (source_fallback != 0 || candidate_fallback != source_fallback) return 1;
    for (int i = 0; i < 32; ++i) {
        if (source_output[i] != candidate_output[i]) return 1;
    }
    std::puts("HETEROREFACTOR_DFS_PUBLIC_ORACLE=passed");
    return 0;
}
