#include <cstdlib>
extern "C" void vector_add(const int[4], const int[4], int[4]);
int main() {
    int a[4] = {-3,0,11,99}, b[4] = {3,7,-4,1}, c[4] = {};
    vector_add(a,b,c);
    const int expected[4] = {0,7,7,100};
    for (int i=0;i<4;++i) if (c[i] != expected[i]) return 3;
    return 0;
}
