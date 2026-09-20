#include <algorithm>
#include <cstdio>

void quickSort(int arr[], int low, int high);

int main() {
    const int inputs[][9] = {
        {19, 17, 15, 12, 16, 18, 4, 11, 13},
        {9, 1, 8, 2, 7, 3, 6, 4, 5},
        {4, 4, 1, 9, 1, 0, -3, 8, 2},
    };
    const int sizes[] = {9, 7, 9};

    for (int test = 0; test < 3; ++test) {
        int actual[9] = {};
        int expected[9] = {};
        for (int index = 0; index < sizes[test]; ++index) {
            actual[index] = inputs[test][index];
            expected[index] = inputs[test][index];
        }
        std::sort(expected, expected + sizes[test]);
        quickSort(actual, 0, sizes[test] - 1);
        for (int index = 0; index < sizes[test]; ++index) {
            if (actual[index] != expected[index]) {
                std::fprintf(
                    stderr,
                    "C2HLSC_QUICKSORT_MISMATCH test=%d index=%d actual=%d expected=%d\n",
                    test,
                    index,
                    actual[index],
                    expected[index]
                );
                return 1;
            }
        }
    }

    std::puts("C2HLSC_QUICKSORT_SOURCE_ORACLE=passed");
    return 0;
}
