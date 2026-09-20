#include <cstdio>

typedef unsigned char present_key_t[10];
typedef unsigned char block_t[8];
extern void present80_encryptBlock(block_t *block, present_key_t *key);

int main() {
    block_t plaintext = {};
    present_key_t key = {};
    present80_encryptBlock(&plaintext, &key);
    const unsigned char expected[8] = {0x55, 0x79, 0xc1, 0x38, 0x7b, 0x22, 0x84, 0x45};
    for (int i = 0; i < 8; ++i) {
        if (plaintext[i] != expected[i]) return 1;
    }
    std::puts("C2HLSC_PRESENT_SOURCE_ORACLE=passed");
    return 0;
}
