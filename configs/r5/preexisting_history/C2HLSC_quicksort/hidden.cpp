void quickSort(int arr[], int low, int high);
void r5_candidate_quickSort(int arr[], int low, int high);

template <int N>
bool equivalent_after_sort(const int (&input)[N]) {
    int reference[N] = {};
    int candidate[N] = {};
    for (int index = 0; index < N; ++index) {
        reference[index] = input[index];
        candidate[index] = input[index];
    }

    quickSort(reference, 0, N - 1);
    r5_candidate_quickSort(candidate, 0, N - 1);
    for (int index = 0; index < N; ++index) {
        if (reference[index] != candidate[index]) {
            return false;
        }
    }
    return true;
}

int main() {
    const int singleton[] = {42};
    const int duplicates[] = {3, -1, 3, 0, -1, 8, 8, 2};
    const int descending[] = {12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1};

    if (!equivalent_after_sort(singleton)) {
        return 1;
    }
    if (!equivalent_after_sort(duplicates)) {
        return 1;
    }
    if (!equivalent_after_sort(descending)) {
        return 1;
    }
    return 0;
}
