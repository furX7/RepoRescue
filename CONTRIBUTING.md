# Contributing

Use Windows and Python 3.12 or newer. Fork the repository and create a branch for
one focused change. From the source root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
python -m unittest discover
```

From the repository root, tests also run without installation or manual PYTHONPATH:
`python -m unittest discover`. Add meaningful tests,
run the full suite, and explain the change and limitations in your PR.
Preserve read-only target handling; execute fixture code only in the audited,
explicit startup-probe regression tests. Discuss
allowlist or safety-policy changes before implementation. Keep runtime dependencies
empty unless a concrete need is approved. Do not commit secrets, caches, venvs or
local build artifacts.

Pack authors should use the experimental public SDK described in
[extension-sdk.md](docs/extension-sdk.md). Default CLI does not load third-party
Packs; explicit discovery executes trusted installed Python code, not sandboxed
code. Keep repair and verification outputs descriptive only.

Optional release build tools belong only in the local virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install build setuptools wheel
.\.venv\Scripts\python.exe -B -m build --no-isolation
```

Inspect the wheel and test installation in a clean temporary venv before a release.
Building does not authorize publishing or tagging.
