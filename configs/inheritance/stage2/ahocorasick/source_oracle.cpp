#include <cstdio>

void process_top(
    int *substring_length_p,
    char *substrings,
    char *query,
    int *substring_indexes,
    int *query_indexes,
    int *fallback
);

int main() {
    int substring_length = 15;
    char substrings[64] = "he%she%his%hers%";
    char query[64] = "ushers%";
    int substring_indexes[64];
    int query_indexes[64];
    for (int i = 0; i < 64; ++i) {
        substring_indexes[i] = 0x11111111;
        query_indexes[i] = 0x22222222;
    }
    int fallback = 0;
    process_top(
        &substring_length,
        substrings,
        query,
        substring_indexes,
        query_indexes,
        &fallback
    );
    const int expected_substrings[4] = {3, 0, 11, -1};
    const int expected_queries[4] = {3, 3, 5, -1};
    if (fallback != 0) return 1;
    for (int i = 0; i < 4; ++i) {
        if (
            substring_indexes[i] != expected_substrings[i]
            || query_indexes[i] != expected_queries[i]
        ) return 1;
    }
    std::puts("HETEROREFACTOR_AHOCORASICK_SOURCE_ORACLE=passed");
    return 0;
}
