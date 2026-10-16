"""Example module exercising several inference paths."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def divide(a: float, b: float) -> float:
    """Divide a by b; raises ZeroDivisionError for b == 0."""
    return a / b


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp value into the inclusive [low, high] range."""
    if not isinstance(value, (int, float)):
        raise TypeError("value must be a number")
    return max(low, min(high, value))


def safe_sqrt(x):
    """Square root of x, or None for negative input."""
    if x < 0:
        return None
    return x ** 0.5


class Stats:
    @staticmethod
    def mean(numbers: list) -> float:
        """Arithmetic mean of a non-empty list of numbers."""
        if not numbers:
            raise ValueError("numbers must not be empty")
        return sum(numbers) / len(numbers)


def _internal_helper(x):  # private: skipped by the generator
    return x * 2
