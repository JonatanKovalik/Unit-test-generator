"""Example module with unannotated and async functions."""

import asyncio


def slugify(text):
    """Convert text to a lowercase hyphenated slug."""
    return "-".join(text.strip().lower().split())


def first_n(items, n):
    """Return the first n elements of a list."""
    return items[:n]


def greet(name: str, greeting: str = "Hello") -> str:
    return f"{greeting}, {name}!"


async def fetch_status(url: str) -> str:
    """Pretend to fetch a status (async)."""
    await asyncio.sleep(0)
    return f"ok:{url}"
