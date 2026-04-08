"""Utility module for cross-file testing."""

from sample import increment


def double(x: int) -> int:
    return increment(x, x)


def triple(x: int) -> int:
    return increment(double(x), x)
