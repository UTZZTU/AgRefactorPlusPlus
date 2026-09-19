#include <cstring>

constexpr int R5_MAX_SIZE = 10;

extern int arr[R5_MAX_SIZE];
extern int r5_candidate_arr[R5_MAX_SIZE];
int reverseKGroup(int n, int k);
int reverseKGroup_hls(int n, int k);

int main() {
    const int input[R5_MAX_SIZE] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
    std::memcpy(arr, input, sizeof(input));
    std::memcpy(r5_candidate_arr, input, sizeof(input));
    const int reference_changes = reverseKGroup(10, 3);
    const int candidate_changes = reverseKGroup_hls(10, 3);
    if (reference_changes != candidate_changes) {
        return 1;
    }
    for (int index = 0; index < R5_MAX_SIZE; ++index) {
        if (arr[index] != r5_candidate_arr[index]) {
            return 1;
        }
    }
    return 0;
}
