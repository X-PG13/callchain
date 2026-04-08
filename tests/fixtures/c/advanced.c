#include "local.h"

int GLOBAL_LIMIT = 10;

struct Node {
    int value;
};

int *identity_ptr(int *value) {
    return value;
}

int check_flags(int a, int b, int c) {
    if ((a && b) || c) {
        return 1;
    }
    return 0;
}

int drive(int value) {
    int *ptr = identity_ptr(&value);
    return check_flags(ptr != 0, value > 0, 0);
}
