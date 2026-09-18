int trap(const int* height);
int trap_hls(const int* height);

int main() {
    const int reference_cases[][12] = {
        {4, 2, 0, 3, 2, 5, 0, 0, 0, 0, 0, 0},
        {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
        {5, 4, 1, 2, 1, 4, 2, 3, 0, 2, 1, 5},
    };
    const int candidate_cases[][12] = {
        {4, 2, 0, 3, 2, 5, 0, 0, 0, 0, 0, 0},
        {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12},
        {5, 4, 1, 2, 1, 4, 2, 3, 0, 2, 1, 5},
    };
    for (int index = 0; index < 3; ++index) {
        if (trap(reference_cases[index]) != trap_hls(candidate_cases[index])) {
            return 1;
        }
    }
    return 0;
}
