#include <iostream>
#include <vector>

namespace math {

class Calculator {
public:
    int add(int a, int b) {
        return a + b;
    }

    int subtract(int a, int b) {
        return a - b;
    }

    static int multiply(int a, int b) {
        return a * b;
    }
};

class AdvancedCalc : public Calculator {
public:
    int power(int base, int exp) {
        int result = 1;
        for (int i = 0; i < exp; i++) {
            result = multiply(result, base);
        }
        return result;
    }

    void demo() {
        int x = add(1, 2);
        int y = power(2, 3);
        std::cout << x << " " << y << std::endl;
    }
};

} // namespace math

void greet() {
    std::cout << "Hello" << std::endl;
}

int main() {
    math::Calculator c;
    c.add(1, 2);
    math::AdvancedCalc ac;
    ac.power(2, 3);
    greet();
    return 0;
}
