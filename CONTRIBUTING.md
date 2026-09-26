# Contributing

Use Windows and Python 3.12 or newer. Fork the repository and create a branch for
one focused change. From the source root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Without installation, use the source test command in README. Add meaningful tests,
run the full suite, and explain the change and limitations in your PR.
Preserve read-only target handling; do not execute fixture project code. Discuss
allowlist or safety-policy changes before implementation. Keep runtime dependencies
empty unless a concrete need is approved. Do not commit secrets, caches, venvs or
local build artifacts.

Optional release build tools belong only in the local virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install build setuptools wheel
.\.venv\Scripts\python.exe -B -m build --no-isolation
```

Inspect the wheel and test installation in a clean temporary venv before a release.
Building does not authorize publishing or tagging.
