#include <stdio.h>
#include "math.h"

typedef struct {
    int x;
    int y;
} Point;

int add(int a, int b) {
    return a + b;
}

int multiply(int a, int b) {
    int result = 0;
    for (int i = 0; i < b; i++) {
        result = add(result, a);
    }
    return result;
}

static int helper(int n) {
    return n + 1;
}

void print_result(int value) {
    printf("Result: %d\n", value);
}

int main() {
    int sum = add(3, 4);
    int prod = multiply(3, 4);
    print_result(sum);
    print_result(prod);
    return 0;
}
