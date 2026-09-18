int trap(const int* height);
int trap_hls(const int* height);

int main() {
    const int reference[12] = {0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1};
    const int candidate[12] = {0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1};
    if (trap(reference) != trap_hls(candidate)) {
        return 1;
    }
    return 0;
}
