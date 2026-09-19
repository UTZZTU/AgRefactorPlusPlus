void quickSort(int arr[], int low, int high);
void r5_candidate_quickSort(int arr[], int low, int high);

int main() {
    constexpr int size = 9;
    int reference[size] = {19, 17, 15, 12, 16, 18, 4, 11, 13};
    int candidate[size] = {19, 17, 15, 12, 16, 18, 4, 11, 13};

    quickSort(reference, 0, size - 1);
    r5_candidate_quickSort(candidate, 0, size - 1);

    for (int index = 0; index < size; ++index) {
        if (reference[index] != candidate[index]) {
            return 1;
        }
    }
    return 0;
}
