#include <cstdio>

int main() {
    for (int i = 0; i < N; ++i) {
        epsilon[i] = (i * 73) % 7 == 0;
    }
    int res_sup = 0;
    int res_inf = 0;
    CumulativeSums(&res_sup, &res_inf);
    if (res_sup != 1 || res_inf != -14285) {
        return 1;
    }
    std::puts("C2HLSC_CUSUMS_SOURCE_ORACLE=passed");
    return 0;
}
