#include <cstdio>
#include <cstddef>

typedef unsigned char data_t[64];
typedef unsigned int state_t[8];
extern void sha256_update(
    data_t *data_int,
    unsigned int *datalen_int,
    state_t *state,
    unsigned long long *bitlen,
    data_t *data,
    size_t len
);
int main() {
    const unsigned char message[] =
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    data_t input = {};
    for (unsigned int i = 0; i < sizeof(message) - 1; ++i) input[i] = message[i];
    data_t buffered = {};
    unsigned int datalen = 0;
    unsigned long long bitlen = 0;
    state_t state = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };
    const state_t expected_state = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    };
    sha256_update(&buffered, &datalen, &state, &bitlen,
                  &input, sizeof(message) - 1);
    if (datalen != sizeof(message) - 1 || bitlen != 0) return 1;
    for (unsigned int i = 0; i < sizeof(message) - 1; ++i) {
        if (buffered[i] != message[i]) return 1;
    }
    for (int i = 0; i < 8; ++i) {
        if (state[i] != expected_state[i]) return 1;
    }
    std::puts("C2HLSC_SHA256_SOURCE_ORACLE=passed");
    return 0;
}
