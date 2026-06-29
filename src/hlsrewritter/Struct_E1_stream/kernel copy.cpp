#include <iostream>

#define N 40010

class ProductOfNumbers {
private:
    int numbers[N];
    int len;

public:
    ProductOfNumbers() : len(0) {}

    void add(int num) {
        if (num == 0) {
            len = 0;
        } else if (len < N) {
            numbers[len++] = num;
        }
    }

    int getProduct(int k) {
        if (len < k) return 0;
        int product = 1;
        for (int i = len - k; i < len; i++) {
            product *= numbers[i];
            if (product == 0) return 0;
        }
        return product;
    }
};

struct InterfaceHandler {
    int& in;
    int& out;
    ProductOfNumbers pon;

    void processCommand() {
        int command = in;  
        int data = in;     

        if (command == 1) {  
            pon.add(data);
            out = -1;  
        } else if (command == 2) {  
            int result = pon.getProduct(data);
            out = result;
        }
    }
};

void top(int& commandDataStream, int& resultStream, int num_commands) {
    int intermediateStream;
    InterfaceHandler handler1{commandDataStream, intermediateStream};
    InterfaceHandler handler2{intermediateStream, resultStream};

    for (int i = 0; i < num_commands; i++) {
        handler1.processCommand();  
        handler2.processCommand();  
    }
}
