"""Compatibility shim for older pip/setuptools that predate PEP 621.

Canonical metadata lives in pyproject.toml; this mirrors it so that
`pip install .` and `pip install -e .` work on any toolchain.
"""

from setuptools import find_packages, setup

setup(
    name="unittest-gen",
    version="0.1.0",
    description="Automated pytest unit-test generator for Python source files",
    packages=find_packages(include=["unittest_gen", "unittest_gen.*"]),
    python_requires=">=3.9",
    entry_points={"console_scripts": ["unittest-gen=unittest_gen.cli:main"]},
    extras_require={"dev": ["pytest>=7"]},
)
