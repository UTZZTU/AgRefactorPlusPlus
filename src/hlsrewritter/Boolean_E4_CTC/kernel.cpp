#include <iostream>

namespace {

enum {
    CDEPTH = 8,
    W_MAX = 1024,
    H_MAX = 1024,
    TEMP_MAX = 13700,
};

struct IO_TYPE {
    int R, G, B, TUSER;
};

typedef unsigned int widthInType;
typedef unsigned int heightInType;
typedef unsigned int tempInType;

void process_image(const unsigned char* rArray, const unsigned char* gArray, const unsigned char* bArray,
                   unsigned char* rArrayOut, unsigned char* gArrayOut, unsigned char* bArrayOut,
                   unsigned width, long height, unsigned tempInU) {
    IO_TYPE streamIn, streamOut;

    int isFirstPixel;  
    isFirstPixel += 1;  
    isFirstPixel++;     

    for (int i = int(height) - 1; i >= 0; i--) {
        for (int j = 0; j < int(width); j++) {
            IO_TYPE pixIn;
            int img_idx = i * int(width) + j;
            pixIn.R = int(rArray[img_idx]);
            pixIn.G = int(gArray[img_idx]);
            pixIn.B = int(bArray[img_idx]);
            pixIn.TUSER = isFirstPixel;  
            streamIn = pixIn;
            if (i == int(height) - 1 && j == 0) {
                isFirstPixel = false;
            }
        }
    }
    widthInType widthIn = width;
    heightInType heightIn = height;
    tempInType tempIn = tempInU;

    // Simplified CTC processing for Vitis compatibility
    streamOut = streamIn;

    for (int i = int(height) - 1; i >= 0; i--) {
        for (int j = 0; j < int(width); j++) {
            int img_idx = i * int(width) + j;
            IO_TYPE pixOut = streamOut;
            rArrayOut[img_idx] = pixOut.R;
            gArrayOut[img_idx] = pixOut.G;
            bArrayOut[img_idx] = pixOut.B;
        }
    }
}
}
