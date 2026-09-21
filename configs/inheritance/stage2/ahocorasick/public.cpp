#include <cstdio>
#include <cstring>

void process_top(
    int *substring_length_p,
    char *substrings,
    char *query,
    int *substring_indexes,
    int *query_indexes,
    int *fallback
);
void process_top_hls(
    int *substring_length_p,
    char *substrings,
    char *query,
    int *substring_indexes,
    int *query_indexes,
    int *fallback
);

int main() {
    int source_length = 15;
    int candidate_length = source_length;
    char source_substrings[64] = "he%she%his%hers%";
    char source_query[64] = "ushers%";
    char candidate_substrings[64];
    char candidate_query[64];
    std::memcpy(candidate_substrings, source_substrings, sizeof(source_substrings));
    std::memcpy(candidate_query, source_query, sizeof(source_query));
    int source_substring_indexes[64];
    int source_query_indexes[64];
    int candidate_substring_indexes[64];
    int candidate_query_indexes[64];
    for (int i = 0; i < 64; ++i) {
        source_substring_indexes[i] = candidate_substring_indexes[i] = 0x11111111;
        source_query_indexes[i] = candidate_query_indexes[i] = 0x22222222;
    }
    int source_fallback = 0;
    int candidate_fallback = 0;
    process_top(
        &source_length,
        source_substrings,
        source_query,
        source_substring_indexes,
        source_query_indexes,
        &source_fallback
    );
    process_top_hls(
        &candidate_length,
        candidate_substrings,
        candidate_query,
        candidate_substring_indexes,
        candidate_query_indexes,
        &candidate_fallback
    );
    if (source_fallback != 0 || candidate_fallback != source_fallback) return 1;
    for (int i = 0; i < 64; ++i) {
        if (
            source_substring_indexes[i] != candidate_substring_indexes[i]
            || source_query_indexes[i] != candidate_query_indexes[i]
        ) return 1;
    }
    std::puts("HETEROREFACTOR_AHOCORASICK_PUBLIC_ORACLE=passed");
    return 0;
}
