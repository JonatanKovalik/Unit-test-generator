# unittest-gen — Automated Unit Test Generator

A CLI tool that reads a Python source file, extracts its public function
signatures, and writes a fully structured **pytest** test file next to it —
covering happy paths, boundary values, empty structures, `None` inputs and
more.

Unlike template generators, unittest-gen can **actually execute** the
functions it finds (snapshot mode): it imports the module, calls each
function with candidate inputs under a timeout, and writes assertions that
encode the *observed* behaviour — exact return values or the specific
exception raised. If execution is impossible or unwanted (`--no-execute`),
it emits contract-style scaffold tests with `TODO` markers instead.

```
$ unittest-gen generate examples/calculator.py
  → parsing examples/calculator.py
  ✓ found 5 public function(s): add, divide, clamp, safe_sqrt, Stats.mean
  → generating tests in snapshot (executing functions) mode
  ✓ wrote examples/test_calculator.py — 16 test(s) across 5 function(s)

  → run the generated tests with:
      python -m pytest examples/test_calculator.py -v
```

## Downloading a prebuilt release

No Python required — grab a standalone binary from
[Releases](https://github.com/JonatanKovalik/Unit-test-generator/releases):

| File | Platform |
| --- | --- |
| `unittest-gen-macos-arm64.tar.gz` | macOS Apple Silicon (M1/M2/M3/M4) |
| `unittest-gen-macos-x86_64.tar.gz` | macOS Intel |
| `unittest-gen-windows-x86_64.zip` | Windows 10/11 x64 |
| `unittest-gen-linux-x86_64.tar.gz` | Linux x64 |
| `unittest-gen.pyz` | Any OS with Python ≥ 3.9 (`python3 unittest-gen.pyz …`) |

Extract the archive, then run `./unittest-gen generate <file.py>`
(`unittest-gen.exe` on Windows). On macOS you may need to clear the
download quarantine once: `xattr -d com.apple.quarantine unittest-gen`.

Releases are built automatically by CI when a `v*` tag is pushed
(see `.github/workflows/release.yml`).

## Installation

Requires Python ≥ 3.9. No runtime dependencies (standard library only);
`pytest` is needed to run the generated tests and the project's own suite.

Runs on **Windows** (10+), **macOS** (Apple Silicon and Intel) and **Linux**.

### macOS (zsh — Apple Silicon / Intel)

```zsh
# from the project root
python3 -m venv .venv
source .venv/bin/activate

pip install .             # installs the `unittest-gen` command
pip install ".[dev]"      # additionally installs pytest (quotes required in zsh)
```

### Windows (PowerShell)

```powershell
# from the project root
py -m venv .venv
.venv\Scripts\Activate.ps1

pip install .             # installs the `unittest-gen` command
pip install ".[dev]"      # additionally installs pytest
```

If activation is blocked by policy:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Windows `cmd.exe` equivalent: `py -m venv .venv` then `.venv\Scripts\activate.bat`.

### Linux (bash / zsh)

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install .             # installs the `unittest-gen` command
pip install ".[dev]"      # additionally installs pytest
```

On Debian/Ubuntu you may first need `sudo apt install python3-venv`.

### Run without installing

From the project root:

```bash
python3 -m unittest_gen generate examples/calculator.py   # macOS / Linux
python -m unittest_gen generate examples\calculator.py    # Windows
```

## Usage

### `unittest-gen generate <file.py>`

Generates `test_<file>.py` alongside the source file.

| Option | Description |
| --- | --- |
| `-o`, `--output PATH` | Write to a custom path instead of `test_<file>.py` |
| `-f`, `--force` | Overwrite an existing test file (refused by default) |
| `--no-execute` | Never import/run the module; emit contract scaffold assertions |

### `unittest-gen inspect <file.py>`

Prints the parsed signatures and inferred parameter types without writing
anything — useful for understanding *why* the generator picks certain values:

```
  add(a: int, b: int) -> int   (line 4)
      a: int (required) [annotation]
      b: int (required) [annotation]
```

## What gets generated

For every public function — module-level `def`s, `async def`s and class
`@staticmethod`s (respecting `__all__` and `_private` conventions) — the
output contains a `TestX` class with:

- **`test_happy_path`** — one call with representative inputs.
- **`test_rejects_edge_inputs`** — parametrized inputs observed to raise,
  asserted via `pytest.raises(expected_exc)`.
- **`test_accepts_edge_inputs`** — parametrized boundary inputs with exact
  expected results.
- **`test_accepts_edge_inputs_returns_typed`** — boundary inputs whose
  results can't be written as literals (asserted via `isinstance`).
- **`test_edge_inputs_unprobed`** — inputs that timed out during generation,
  emitted as `pytest.mark.skip` for transparency.

Example (snapshot mode):

```python
@pytest.mark.parametrize(
    "a, b, expected_exc",
    [
        pytest.param(None, 10, TypeError, id="a=None"),
        pytest.param(2.5, 0.0, ZeroDivisionError, id="b=zero"),
    ],
)
def test_rejects_edge_inputs(self, a, b, expected_exc):
    """Rejected boundary/invalid inputs raise a clear error."""
    with pytest.raises(expected_exc):
        divide(a, b)
```

> Snapshot assertions record what the code *does*, not necessarily what it
> *should* do. Review them — a passing snapshot test can also pin a bug.

## How it works

```
source.py ──▶ analyzer ──▶ infer ──▶ executor ──▶ generator ──▶ test_source.py
             (ast parse)  (types)   (probe)      (render)
```

| Module | Responsibility |
| --- | --- |
| `unittest_gen/analyzer.py` | `ast`-based extraction of public functions: params, kinds, defaults, annotations, return types, docstrings, `__all__` filtering |
| `unittest_gen/infer.py` | Assigns each parameter a kind (`int`, `str`, `list`, …) from annotations → defaults → `isinstance`/method/operator usage in the body → naming conventions; maps kinds to happy-path and edge-case value tables |
| `unittest_gen/executor.py` | Imports the target module, probes candidate calls under a 2 s per-call timeout with muted stdio, records return literals (round-tripped via `ast.literal_eval`) or exception classes; detects non-deterministic results by calling twice |
| `unittest_gen/generator.py` | Resolves `unknown` parameter types by trial calls, groups outcomes, renders the pytest module |
| `unittest_gen/cli.py` | `generate` / `inspect` commands, colored status logs, exit codes |

**Edge-case coverage** comes from per-kind value tables: `None`, empty
structures (`""`, `[]`, `{}`, `set()`), boundaries (`0`, `-1`, `2**63 - 1`,
`1e308`), unicode and long strings, single-element and large collections.

**Parameters without annotations** are still handled: body usage
(`s.strip()` → `str`, `isinstance(x, dict)` → `dict`, `x * 2` → `int`) and
naming conventions (`count` → `int`, `is_active` → `bool`, `items` → `list`)
feed the inference, and truly unknown parameters are resolved by trying
candidate values until the function accepts one.

## Limitations and roadmap

- Instance methods are skipped (constructing arbitrary objects safely is
  out of scope); `@staticmethod`s are covered.
- Probing runs your code — only generate tests for code you would run.
  Side-effecting or non-deterministic functions get type-level assertions.
- The per-call timeout uses `SIGALRM` on Unix; on Windows (or off the main
  thread) it falls back to a daemon thread, so a call that times out keeps
  running in the background until the generator exits.
- Planned: fixture detection, coverage-gap analysis, JS/Jest backend.

## Development

```bash
python -m pytest tests/ -v   # the tool's own test suite (with venv activated)
```

## Disclaimer

unittest-gen is provided **"as is", without warranty of any kind** —
express or implied. The authors are not responsible for any damage or
data loss arising from its use.

**Snapshot mode executes the code it analyzes.** Only run the generator
on source files you would run yourself — never on untrusted code.
Generated assertions record *observed* behavior, which may include
bugs; review them before committing.

Use at your own risk. See [LICENSE](LICENSE) (Apache-2.0) §7–8 for the
full warranty disclaimer and limitation of liability.

## License

Apache-2.0 — © 2026 JonatanKovalik. See [LICENSE](LICENSE).
