#include <cstring>

int length(char* value);
int length_hls(char* value);

int main() {
    char reference[] = "abcabcbb";
    char candidate[sizeof(reference)];
    std::memcpy(candidate, reference, sizeof(reference));
    if (length(reference) != length_hls(candidate)) {
        return 1;
    }
    return 0;
}
