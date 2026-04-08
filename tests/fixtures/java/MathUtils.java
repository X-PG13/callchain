package com.example;

import java.util.Arrays;
import java.util.List;

public class MathUtils {
    public static int increment(int a, int b) {
        return a + b;
    }

    public void processAll(List<Integer> items) {
        items.forEach(item -> System.out.println(item));
        items.stream().map(MathUtils::increment);
    }

    public static void main(String[] args) {
        MathUtils utils = new MathUtils();
        utils.processAll(Arrays.asList(1, 2, 3));
    }
}
