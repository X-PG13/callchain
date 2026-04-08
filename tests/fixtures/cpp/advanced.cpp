#include "helper.hpp"
#include <vector>

int GLOBAL_COUNTER = 0;

struct Data {
    int value;
};

template <typename T>
T square(T value) {
    return value * value;
}

class Widget {
public:
    static int declared(int value);
    int invoke();
};

int Widget::declared(int value) {
    return square<int>(value);
}

int Widget::invoke() {
    Widget helper;
    helper.declared(1);
    Widget::declared(2);
    return square<int>(3);
}
