"""Sample Python file for testing."""

import os
from pathlib import Path


GLOBAL_VAR = 42


class Calculator:
    """A simple calculator."""

    def __init__(self, value: int = 0):
        self.value = value

    def add(self, x: int) -> int:
        self.value = increment(self.value, x)
        return self.value

    async def async_add(self, x: int) -> int:
        return self.value + x


def increment(a: int, b: int) -> int:
    return a + b


def main():
    calc = Calculator(10)
    result = calc.add(5)
    print(result)


@staticmethod
def helper():
    return increment(1, 2)
