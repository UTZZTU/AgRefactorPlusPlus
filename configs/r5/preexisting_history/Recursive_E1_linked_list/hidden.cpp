#include <cstring>

constexpr int R5_MAX_SIZE = 10;

extern int arr[R5_MAX_SIZE];
extern int r5_candidate_arr[R5_MAX_SIZE];
int reverseKGroup(int n, int k);
int reverseKGroup_hls(int n, int k);

int main() {
    const int lengths[] = {1, 7, 10};
    const int groups[] = {1, 2, 4};
    const int inputs[][R5_MAX_SIZE] = {
        {42, -1, -1, -1, -1, -1, -1, -1, -1, -1},
        {9, -3, 5, 5, 0, 12, -8, 77, 77, 77},
        {10, 9, 8, 7, 6, 5, 4, 3, 2, 1},
    };
    for (int case_index = 0; case_index < 3; ++case_index) {
        std::memcpy(arr, inputs[case_index], sizeof(arr));
        std::memcpy(r5_candidate_arr, inputs[case_index], sizeof(r5_candidate_arr));
        const int reference_changes = reverseKGroup(
            lengths[case_index], groups[case_index]
        );
        const int candidate_changes = reverseKGroup_hls(
            lengths[case_index], groups[case_index]
        );
        if (reference_changes != candidate_changes) {
            return 1;
        }
        for (int index = 0; index < R5_MAX_SIZE; ++index) {
            if (arr[index] != r5_candidate_arr[index]) {
                return 1;
            }
        }
    }
    return 0;
}
