#include <iostream>

void regUpdate(const bool input, const bool terminate, bool reg[3], bool output[2])
{
    bool regTemp[3] = {reg[0], reg[1], reg[2]};
    output[0] = terminate ? regTemp[2] ^ regTemp[1] : input;
    output[1] = terminate ? regTemp[2] ^ regTemp[0] : regTemp[1] ^ regTemp[0] ^ input;
    reg[2] = regTemp[1];
    reg[1] = regTemp[0];
    reg[0] = terminate ? false : regTemp[2] ^ regTemp[1] ^ input;
}
