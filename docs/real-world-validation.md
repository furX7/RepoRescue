# Real-world validation observations

These records capture what RepoRescue did on real projects at a point in time.
They are observations, not roadmap commitments or promises of supported coverage.

## a2aproject/a2a-samples #529

- **Date:** 2026-09-27
- **RepoRescue:** 0.3.0a1
- **Result:** ❌ Missed diagnosis
- **Issue:** [ModuleNotFoundError: No module named `a2a.server.apps`](https://github.com/a2aproject/a2a-samples/issues/529)

### Reported problem and confirmed cause

The issue reported that the Hello World sample imported
`A2AStarletteApplication` from `a2a.server.apps` with `a2a-sdk==1.0.0` and
received `ModuleNotFoundError`. The SDK 1.0 migration removed its application
wrapper classes and moved server setup to Starlette route factories. The old
sample import is therefore incompatible with SDK 1.0.

The failure was independently reproduced against the unmodified published
1.0.0 wheel. This was a minimal import reproduction on Python 3.13.1, not a
full reproduction of the reporter's Python 3.14.4 environment. The historical
sample snapshot from the issue date retained the old import and allowed SDK
versions from `1.0.0a0`. The current sample has since moved to `__main__.py`,
uses route factories, and pins SDK 1.1.0.

### RepoRescue observation

Default diagnosis on both the current sample and the issue-date snapshot found
no problems and reported JSON status `healthy`. It detected a Python project
and the available Python 3.13.1 interpreter, but did not establish that the
application starts or that its imports match the installed SDK API. The
historical snapshot still contained the failing import. The `healthy` result
reflects the limited checks performed; it did not rule out the reported error.

The startup probe was skipped because both sample versions use `__main__.py`;
RepoRescue 0.3.0a1 only supports the root-level `main.py` entrypoint. The run
therefore did not capture the application's traceback. No project files were
changed.

### Gaps observed

- Startup coverage for Python entrypoints such as `__main__.py`.
- A way to provide an existing traceback or log as diagnostic evidence.
- Evidence for installed distribution versions and the module actually loaded.
- Correlation between source imports, declared/locked dependency versions, and
  known API removals or incompatibilities.

These are candidate capability gaps surfaced by this observation. This record
does not add an A2A-specific rule or commit any item to the roadmap. Any future
work should first evaluate general Python Pack capabilities for entrypoint
detection, import evidence, dependency metadata, and version/API correlation.

## Cinnamon/kotaemon #833

- **Date:** 2026-09-27
- **RepoRescue:** 0.3.0a1
- **Result:** 🟡 Partial diagnosis
- **Issue:** [Windows install fails with `ImportError: cannot import name 'HfFolder'`](https://github.com/Cinnamon/kotaemon/issues/833)

### Reported problem

A fresh Windows install of Kotaemon 0.11.3 creates its Conda environment, then
fails while launching the UI with `ImportError: cannot import name 'HfFolder'
from 'huggingface_hub'`. The issue attributes this to Gradio importing the
removed symbol after an unpinned installation selects a newer Hub release. The
reported environment does not specify exact Python, Gradio, or Hub versions.
The tagged Windows installer selects Python 3.10 and starts `app.py` through
`scripts/run_windows.bat`.

### Minimal reproduction

The full installer was not run because it provisions Conda, a large dependency
set, and local model services. Instead, a separate minimal project imported
`HfFolder` from the unmodified official `huggingface-hub==1.0.0` wheel. This
reproduced the same ImportError on Python 3.13.1. The import was taken from
Gradio 4.44.1's `oauth.py`; that Gradio version is allowed by Kotaemon's
`>=4.31.0,<5` requirement, but is not confirmed as the reporter's version.
This verifies the reported API failure mechanism, not a full Kotaemon startup
or the reporter's exact dependency resolution.

### RepoRescue observation

The default run on the unmodified Kotaemon v0.11.3 clone reported `healthy`,
found no problems, and said no supported root `main.py` entrypoint was present.
Its Windows batch launcher and `app.py` are outside the current startup probe
allowlist, so no probe was run on the original project.

On the isolated minimal project, default diagnosis also reported `healthy` and
proposed the supported `main.py` startup probe. With explicit startup probing,
RepoRescue captured the real `HfFolder` ImportError and diagnosed a Python
symbol import failure. The root-cause chain stopped at “the providing
distribution and underlying cause are not established.” The repair preview
recommended reviewing the environment and module API; verification steps were
planned but not run. RepoRescue did not identify a version incompatibility or
recommend a specific version change.

An initial minimal attempt imported both names from Gradio's source and stopped
earlier because `whoami` lazy loading needed `httpx`, which was absent from the
test interpreter. That output is not evidence about the reported Kotaemon
environment; the final minimal reproduction isolated only the reported
`HfFolder` symbol.

### Independently supported root cause

Gradio's source imports `HfFolder`, and its dependency metadata allows
`huggingface-hub>=0.19.3` without an upper bound. The Hub 1.0 migration removed
`HfFolder`; the earlier 0.35.3 release still exports it. These facts support a
transitive Gradio-to-Hugging-Face-Hub API incompatibility as the cause of the
reported error, while the exact versions installed by the reporter remain
unknown.

Kotaemon's checked-in `uv.lock` records Gradio 4.39.0 and Hub 0.35.3, but its
Windows installer installs dependencies with `pip` and does not use that lock
file. A declared range, a lock-file selection, and the packages actually
installed by a particular installer are distinct facts; the lock file alone
does not establish the installer's environment.

### Gaps observed

The following gaps also appeared in case #1:

- Support for more Python startup entrypoints.
- A way to supply the user's existing traceback or logs.
- Evidence for installed distribution versions and loaded module origins.
- Correlation between imports, dependency versions, and API compatibility.

This case also makes two related gaps clearer: tracing a failure through
transitive dependency consumers, and comparing declared versions, lock-file
versions, and versions installed by the actual package-manager path.

These are observations for future evaluation, not automatic roadmap additions.
They do not call for a Kotaemon- or Gradio-specific rule in Core. General
dependency, environment, and import evidence belongs in the Python Pack;
framework-specific API compatibility knowledge can live in a relevant Pack.
Core should continue to own shared evidence, safety, and execution policy.

## Comfy-Org/ComfyUI #15784

- **Date:** 2026-09-27
- **RepoRescue:** 0.3.0a1
- **Result:** 🟡 Partial diagnosis
- **Issue:** [ComfyUI nightly startup fails importing `ColorPrimaries`](https://github.com/Comfy-Org/ComfyUI/issues/15784)

### Reported problem and source revision

The issue describes a startup `ImportError` for `ColorPrimaries` from
`av.video.reformatter` after updating to a ComfyUI nightly around Aug 20–21,
2026. It does not give an exact commit SHA. Source history points to
`dcbcf8c2e10ba618ad38cc6eebfe54b099fec11a` (Aug 20, HDR video support), whose
`comfy_api/latest/_input_impl/video_types.py` contains the import from the
traceback and is loaded through the `main.py` → `execution` → `nodes` startup
path.

The issue says disabling custom nodes avoids the crash, but the selected source
imports `comfy_api.version_list` unconditionally from `nodes.py`; that custom
node condition was not independently confirmed. The full ComfyUI GPU/model
environment was not installed.

### Reproduction and RepoRescue observation

On the untouched ComfyUI clone, default diagnosis reported `healthy` and
proposed the supported root `main.py` startup probe. The probe ran, but this
machine stopped earlier on missing `sqlalchemy`; that finding describes the
validation environment and did not reach the reported PyAV error.

A separate minimal `main.py` project used the first three imports from the
actual ComfyUI source and the unmodified official Windows PyAV wheels. With
PyAV 16.0.1, default diagnosis reported `healthy`; startup probing captured the
actual `ColorPrimaries` `ImportError` and correctly classified it as a Python
symbol import failure. The root-cause chain stopped at “the providing
distribution and underlying cause are not established.” Repair Preview
recommended environment/API review; verification was planned, not run.

The same import succeeded with PyAV 17.0.0 and 18.1.0. These are minimal import
checks, not a full ComfyUI startup or GUI verification.

### Independently supported root cause

The selected ComfyUI `requirements.txt` declares `av>=16.0.0`, while the
video API imports `ColorPrimaries`, `ColorRange`, and `ColorTrc`. In the tested
official wheels, PyAV 16.0.1 provides `ColorRange` but lacks `ColorPrimaries`
and `ColorTrc`; versions 17.0.0 and 18.1.0 provide all three. PyAV's 17.0.0
release notes identify `ColorPrimaries` and `ColorTrc` as additions. The
failure is therefore an API capability mismatch: the declared lower bound
allows a version that lacks symbols required by the source. The issue's claim
that newer PyAV releases removed these symbols conflicts with the tested
versions and official release notes.

### Gaps observed

These gaps repeat cases #1 and #2:

- Accepting a user's existing traceback or logs as evidence.
- Collecting structured installed distribution version and module-origin facts.
- Correlating source imports with dependency versions and the required API.

The earlier entrypoint gap does not apply here: RepoRescue recognized and ran
root `main.py`. This case adds a general version-floor gap: a package can
satisfy its declared minimum version while lacking a symbol the source now
requires. It also shows that an incomplete local environment can fail earlier
than the target error and must not be mistaken for a full issue reproduction.

These are observations for future evaluation, not automatic roadmap additions.
Generic Python dependency and API evidence belongs in the Python Pack;
ComfyUI/PyAV-specific compatibility knowledge can live in a relevant Pack.
Core retains shared evidence, provenance, safety and execution policy. This
record does not add a ComfyUI-specific rule or commit any item to the roadmap.

## NousResearch/hermes-agent #123185

**Rating: 🟡 Partial diagnosis**

### Issue and reproduction

On Windows, the project virtual environment used CPython 3.11.16, while
launching `hermes` through `PATH` selected a standalone CPython 3.14.7. The
startup failure was `ModuleNotFoundError: No module named
'pydantic_core._pydantic_core'`; launching with the project venv's Python
worked. The issue reports the problem against Hermes commit `fe417523fe`.

A minimal trusted reproduction used an intact official `pydantic_core` wheel
for CPython 3.12.14 in the project's `venv`, and CPython 3.13.1 as the
external launcher. The Windows gateway venv-import setup logic from the
project source was applied without modification in a minimal root `main.py`.
The external interpreter then failed to import the native extension from the
other interpreter's site-packages; running the same files with the venv's
Python succeeded. This reproduces the interpreter/site-packages ABI failure,
not Hermes' complete startup and crash-loop behavior. The reproduction's
Python versions differ from the issue reporter's versions.

### RepoRescue result

RepoRescue 0.3.0a1's default diagnosis identified the external interpreter as
different from the project venv and reported `project_interpreter_different`.
With `--run-startup-probe`, it also captured the
`pydantic_core._pydantic_core` import failure and reported the failed probe.
It did not connect these observations into a root-cause chain.

When two local virtual environments were present, RepoRescue marked the
environment evidence `ambiguous` but still returned top-level `healthy` with
no diagnosis. When run under the matching project interpreter, the default
diagnosis was healthy and the startup probe succeeded.

### Independently supported root cause and gaps

The issue's working project environment and the interpreter selected for
startup had different CPython versions. The imported `pydantic_core` native
extension came from site-packages built for another CPython ABI. This is an
interpreter, package-path and native-extension ABI mismatch, rather than a
missing or inherently broken `pydantic_core` release.

RepoRescue correctly detected the interpreter/venv mismatch and the startup
import failure, but missed the causal connection between them. It also lacked
evidence from `pyvenv.cfg`, the resolved package/module path, and native
extension ABI compatibility. The `ambiguous` environment state should remain
visible in the overall result instead of being masked by top-level `healthy`.

The missing traceback/log ingestion and installed distribution/module-origin
evidence also appeared in earlier cases. This case adds a more specific
environment-to-import causal correlation gap and the need to inspect venv
configuration, package paths and native extension ABI evidence. These are
observations for evaluation only; they do not automatically enter the roadmap.

## ANRAR4/AutoBTD6 #36

- **Date:** 2026-09-27
- **RepoRescue:** 0.3.0a1
- **Result:** ❌ Missed diagnosis
- **Issue:** [Python 3.13 cannot install TensorFlow](https://github.com/ANRAR4/AutoBTD6/issues/36)

The reporter said Python 3.13 could not install TensorFlow and reported Python
3.12.5 as working. The project has no `requires-python`; its unpinned
`requirements.txt` lists TensorFlow. At the issue date, TensorFlow had no
matching CPython 3.13 artifact. TensorFlow's own `Requires-Python: >=3.9` did
not exclude Python 3.13. A historical pip candidate-resolution check failed
for CPython 3.13 and succeeded when targeting the CPython 3.12 Windows wheel.
This was a minimal resolver reproduction, not a full requirements install.

RepoRescue returned `healthy` without a diagnosis. It correctly refrained from
inventing a project Python version limit, but did not inspect dependency
metadata or available wheel compatibility. The missing evidence layer is
dependency distribution metadata: Python ABI tags and platform compatibility,
in addition to `Requires-Python`. There was no root `main.py`; the startup
probe was inapplicable to this install-time failure and was not run.

This reinforces the observations on traceback/log input and dependency evidence.
Unlike cases where imports and installed modules could be inspected, this
failed install requires package-index and artifact-availability evidence.
These findings are observations, not automatic roadmap additions.

## First 5 Cases — Observed Patterns

Counts below mark cases where the documented observation explicitly surfaced
the pattern. They describe repeated evidence, not approved roadmap items.

| Observed pattern | Cases | Count | Signal |
| --- | --- | ---: | --- |
| Existing traceback/log input | #1, #2, #3, #4 | 4/5 | Strong repeated signal |
| Installed distribution and loaded-module source evidence | #1, #2, #3, #4 | 4/5 | Strong repeated signal |
| Declared, locked, resolved and installed version distinction | #1, #2, #3, #5 | 4/5 | Strong repeated signal; the version sources vary by case |
| Dependency/API/ABI compatibility evidence | #1, #2, #3, #4, #5 | 5/5 | Strong repeated signal |
| Cross-evidence root-cause correlation | #1, #2, #3, #4, #5 | 5/5 | Strong repeated signal |
| Healthy/unverified status semantics | #1, #2, #3, #4, #5 | 5/5 | Strong repeated signal |
| Startup entrypoint coverage beyond root `main.py` | #1, #2 | 2/5 | Repeated, not yet as broad |

Case #5 did not provide an install traceback, and installation did not succeed;
therefore it is not counted for the first two patterns. Counts record observed
gaps across these cases only and do not establish roadmap priority.
