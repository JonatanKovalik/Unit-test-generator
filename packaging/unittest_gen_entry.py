"""Standalone entry point for PyInstaller (`pyinstaller --onefile`).

`unittest_gen/__main__.py` cannot be frozen directly because it uses a
relative import; this wrapper uses an absolute import instead.
"""

from unittest_gen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
