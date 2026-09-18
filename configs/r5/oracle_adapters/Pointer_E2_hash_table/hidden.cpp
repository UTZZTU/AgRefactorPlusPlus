#include <cstring>

int length(char* value);
int length_hls(char* value);

int main() {
    const char* inputs[] = {"", "bbbbb", "pwwkew", "dvdf", "anviaj"};
    for (const char* input : inputs) {
        char reference[32] = {};
        char candidate[32] = {};
        std::strncpy(reference, input, sizeof(reference) - 1);
        std::strncpy(candidate, input, sizeof(candidate) - 1);
        if (length(reference) != length_hls(candidate)) {
            return 1;
        }
    }
    return 0;
}
