package com.example;

import java.util.List;

public class Calculator {
    private int value;

    public Calculator(int value) {
        this.value = value;
    }

    public int add(int x) {
        this.value = MathUtils.increment(this.value, x);
        return this.value;
    }

    public int subtract(int x) {
        return this.value - x;
    }

    public static void main(String[] args) {
        Calculator calc = new Calculator(10);
        calc.add(5);
        System.out.println(calc.subtract(3));
    }
}
