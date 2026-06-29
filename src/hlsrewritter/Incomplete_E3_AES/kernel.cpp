#include <iostream>
#include <cstring>
#include <sstream>

using namespace std;

void aes_encrypt(const string &input, string &output, const string &key_str, int &msg_inp_chan, int &msg_out_chan, int aes_mode) {
    int key_enc_chan;
    int key;
    istringstream hex_chars_stream(key_str);
    int i = 0;
    unsigned int c;
    while (hex_chars_stream >> hex >> c) {
        key = c;
        i++;
    }
    key_enc_chan = key;

    // Define a condition variable with only 2 possible values
    bool cond = false;  

    int size = input.size();
    int paddedMessageLen = ((size + 15) / 16) * 16;

    // Preparing and writing data on Input channel
    int count = 0;
    for (int i = 0; i < paddedMessageLen; i++) {
      int inp_tmp;
      if (i < size) {
        inp_tmp = (int)(input[i]);
      } else {
        inp_tmp = 0;
      }
      count++;
      if (count == 16) {
        msg_inp_chan = inp_tmp;
        count = 0;
      }
    }

    // Incomplete switch statement for error injection
    switch (cond) {
    case 0:  // Only handle one case, leaving the other case which could lead to unintended operation
        // Creating Object for encryption for required AES Mode
        if (aes_mode == 0) {
            for (int i = 0; i < paddedMessageLen; i += 16) {
                msg_out_chan = msg_inp_chan;
            }
        } else if (aes_mode == 1) {
            for (int i = 0; i < paddedMessageLen; i += 16) {
                msg_out_chan = msg_inp_chan;
            }
        } else if (aes_mode == 2) {
            for (int i = 0; i < paddedMessageLen; i += 16) {
                msg_out_chan = msg_inp_chan;
            }
        }
        break;
    case 1:
        break;
    }

  // Receiving Encrypted message
  output.resize(paddedMessageLen);
  count = 0;
  for (int i = 0; i < paddedMessageLen; i += 16) {
    int out_tmp = msg_out_chan;
    for (int j = 0; j < 16; j++) {
      output[i + j] = (char)out_tmp;
    }
    count += 16;
  }
}

// Function to perform AES decryption
void aes_decrypt(const string &input, string &output, const string &key_str, int &msg_aes_chan, int &msg_out_chan, int aes_mode) {
  int key_dec_chan;
  int key;
  istringstream hex_chars_stream(key_str);
  int i = 0;
  unsigned int c;
  while (hex_chars_stream >> hex >> c) {
    key = c;
    i++;
  }
  key_dec_chan = key;

  // Writing encrypted message to input channel
  int paddedMessageLen = input.size();
  int count = 0;
  for (int i = 0; i < paddedMessageLen; i++) {
    int inp_tmp;
    inp_tmp = (int)(input[i]);
    count++;
    if (count == 16) {
      msg_aes_chan = inp_tmp;
      count = 0;
    }
  }

  // Creating Object for decryption for required AES Mode
  if (aes_mode == 0) {
    for (int i = 0; i <= paddedMessageLen; i += 16) {
      msg_out_chan = msg_aes_chan;
    }
  } else if (aes_mode == 1) {
    for (int i = 0; i <= paddedMessageLen; i += 16) {
      msg_out_chan = msg_aes_chan;
    }
  } else if (aes_mode == 2) {
    for (int i = 0; i <= paddedMessageLen; i += 16) {
      msg_out_chan = msg_aes_chan;
    }
  }

  // Receiving Decrypted message
  output.resize(paddedMessageLen);
  count = 0;
  for (int i = 0; i < paddedMessageLen; i += 16) {
    int out_tmp = msg_out_chan;
    for (int j = 0; j < 16; j++) {
      output[i + j] = (char)out_tmp;
    }
    count += 16;
  }
}
