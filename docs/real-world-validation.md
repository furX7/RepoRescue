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

## First 5 Cases — v0.4 Phase 1 Regression Summary

- **Date:** 2026-09-27
- **Baseline:** `15cd8fe881902ae09b59261720c5e3fec105135b`, with HEAD/main/origin/main equal and the original worktree clean.
- **Scope:** completed Phase 1 Steps 1–6; reported tool version remains 0.3.0a1 and JSON schema remains 0.2. This is validation, not a release or feature change.
- **Method:** reuse the five original temporary project snapshots, official wheels, minimal scripts, and reports. No fixture edits, installs, downloads, provider substitutions, or dependency upgrades. SHA-256 comparison of 1,823 source/configuration/wheel/native-extension files found no changes during the primary reruns.
- **Runs:** 24 primary RepoRescue invocations, one separate historical CPython 3.12 download-log ingestion control, and a repeated independent Case #1 import. Default runs use the original RepoRescue Python 3.13.1 venv; Case #4 matched controls use its original project Python 3.12.14 venv. `PYTHONDONTWRITEBYTECODE=1` prevents new bytecode. Raw commands, JSON, stdout/stderr, hashes, and copied original tracebacks are retained in the regression artifact directory listed below.
- **Rating rule:** Correct requires the confirmed case mechanism, not just detection of its exception. Partial captures the real failure/context without establishing that mechanism. Missed means no diagnosis of the reported failure. New ingestion runs are explicitly additional workflows; they are not presented as improvements in unchanged default runs.

| Case | Original rating → current rating | Rating trend | Actual contribution from Steps 1–6 |
| --- | --- | --- | --- |
| #1 a2a-samples #529 | ❌ Missed → 🟡 Partial | Improved | Step 1: inconclusive/unverified default; Step 2: existing traceback diagnoses missing module; Step 4: separate declared/locked SDK versions. Steps 3/5 cannot establish the historical provider; Step 6 adds no correlation. |
| #2 kotaemon #833 | 🟡 Partial → 🟡 Partial | Unchanged rating; workflow/evidence improved | Steps 1/2: explicit incomplete checks and traceback ingestion on the original project; Step 4: minimal declaration distinct from unknown installed version; Step 5: API limitation; Step 6: low-confidence inconclusive API context. No deeper API cause established. |
| #3 ComfyUI #15784 | 🟡 Partial → 🟡 Partial | Unchanged rating; workflow/evidence improved | Steps 1/2: original-project traceback ingestion; Step 4: real av version floor and minimal pins; Step 5: interpreter facts/API limitation; Step 6: inconclusive API context. Existing 17/18 success controls still succeed. |
| #4 hermes-agent #123185 | 🟡 Partial → 🟡 Partial | Unchanged rating; evidence improved | Step 1: ambiguity explicitly inconclusive; Step 2: supplied traceback; Steps 3/4: real installed pydantic_core 2.27.2 in the matched interpreter; Step 5: its actual WHEEL tags; Step 6: inconclusive interpreter context. No ABI root cause proven. |
| #5 AutoBTD6 #36 | ❌ Missed → ❌ Missed | Unchanged rating; facts/assessment improved | Steps 1/2: inconclusive and supplied pip failure facts; Step 4: unconstrained tensorflow declaration distinct from unknown installed value. Step 5 parses a wheel only in the separate successful-download log control; Step 6 produces no failure correlation. |

### Conditions and execution boundaries

The unchanged materials are under `C:\Users\MSI\AppData\Local\Temp`:

| Case | Original material directory | Current reruns |
| --- | --- | --- |
| #1 | `reporescue-issue529-20260927` | Historical and current Hello World defaults; historical snapshot with exact saved independent traceback; same independent official-wheel import repeated. |
| #2 | `reporescue-kotaemon833-20260927` | Kotaemon default and supplied traceback; original `minimal-symbol-repro` default, supplied traceback, and startup. |
| #3 | `reporescue-comfy15784-20260927` | ComfyUI default and supplied target traceback; original av 16.0.1 minimal default/traceback/startup; original av 17.0.0 and 18.1.0 startup controls. |
| #4 | `reporescue-hermes123185-20260927` | Single environment default/traceback/startup under external 3.13.1; two-environment ambiguous default; matched 3.12.14 default/startup. |
| #5 | `reporescue-autobtd6-36-20260927-070828` | Current and issue-era defaults; exact saved CPython 3.13 pip failure log; separate saved CPython 3.12 successful-download log control. |

The exact old stderr was copied from `independent-import.json` (#1),
`symbol-startup.json` (#2), `minimal-startup.json` (#3), and
`different-startup.json` (#4). These are real saved minimal failures, not invented
full-application traces. Their ingestion against the original projects is a new
supported workflow, and does not establish that those projects were executed now.

Startup runs are limited to the original inspected minimal import scripts.
The Hermes fixture has no `.pth` files in its selected site-packages; its copied
guard, environment updates, and native import run only in the bounded child.
Full Kotaemon installation/UI, Hermes gateway, game automation, and ComfyUI
startup were not run. The original full ComfyUI startup stopped earlier at
missing sqlalchemy; the safe target-only scripts remain the comparison for the
PyAV issue. Cases #1/#2/#5 lack a supported root `main.py`. No new entrypoint or
full environment was created. These limits prevent a claim of full application
reproduction or of the reporters' exact environments.

Regression artifacts are retained at
`C:\Users\MSI\Documents\Codex\2026-09-27\referenced-chatgpt-conversation-this-is-an-2\outputs\first-five-regression`.
`run-manifest.json` lists all CLI commands and exits; `case*-*.json` and `.txt`
retain primary reports and terminal output; `case5-cp312-log-control.json` is
the additional control. `fixture-hashes-before.json` records preserved inputs.

### Case #1 — a2aproject/a2a-samples #529

**Current rating: 🟡 Partial diagnosis; improved from Missed.** Both unchanged
default runs still have no diagnosis. Their legacy JSON status is `healthy`,
but assessment/CLI say inconclusive, incomplete, and unverified. The supplied
real traceback now gives an ERROR and exit 1, identifying `a2a.server.apps`.
The independent import against the same official SDK 1.0.0 wheel still fails
with the same ModuleNotFoundError.

Evidence includes supplied-log provenance, the import exception, current
interpreter, module origin `not_found`, distribution mapping `unavailable`,
declared SDK `>=1.0.0a0`, and historical uv.lock SDK `1.0.0a0`. The separate
current sample declares `==1.1.0`. None of these is reported as the installed
SDK or a resolved SDK 1.0.0. The independent reproduction's 1.0.0 wheel is not
on the collector's normal path and is not borrowed as current installed evidence.

**Current Root Cause Chain:** explicit missing-module exception → requested
import did not complete → provider and underlying cause unconfirmed. There is
no Step 6 correlation. It still does not establish the SDK 1.0 wrapper removal
or route-factory migration. Existing non-blocking gaps remain entrypoint
coverage and historical/provider API evidence. The new workflow improves
failure detection; default diagnosis and causal depth remain limited.

### Case #2 — Cinnamon/kotaemon #833

**Current rating: 🟡 Partial diagnosis; unchanged rating.** The original clone's
default is inconclusive without findings. Its supplied traceback and the
unchanged minimal startup both identify the real `HfFolder` ImportError as
ERROR/exit 1. The minimal wheel is still Hub 1.0.0; the full installer and the
reporter's unknown resolved versions are not reproduced.

Evidence includes the exception, supplied-log or captured-child origin, current
interpreter, module origin `not_found`, unavailable distribution mapping,
minimal declared Hub `==1.0.0`, unknown installed Hub, and API `unavailable`
with `no_plain_python_source`. The root workspace declares `kotaemon[all]` and
`ktem`; it does not expose nested Gradio/Hub declarations to the bounded root
collector. The 1,269,593-byte uv.lock exceeds the 256 KiB source limit and is
explicitly unavailable. The independently known Gradio 4.44.1, lock versions,
and Hub removal are not falsely emitted as current observed provider facts.

**Current Root Cause Chain:** cannot-import-name exception → HfFolder import
did not complete → runtime provider/cause unconfirmed. Step 6 adds low-confidence
`dependency_api`, status `inconclusive`, with an empty correlation chain.
Traceback ingestion on the original project and explicit limitations improve
the workflow, but no Hub/Gradio causal chain is established. Known limitations
are static plain-source API coverage and bounded workspace/source ingestion;
the real large lockfile demonstrates their impact for Stabilization assessment.

### Case #3 — Comfy-Org/ComfyUI #15784

**Current rating: 🟡 Partial diagnosis; unchanged rating.** Original-project
traceback ingestion and the unchanged av 16.0.1 minimal startup identify
`ColorPrimaries` from `av.video.reformatter`; the 17.0.0 and 18.1.0 controls
still complete successfully without ERROR or WARNING. The real project
requirements declare `av>=16.0.0`; minimal projects separately declare their
exact av versions. No version floor is silently strengthened.

Evidence includes current interpreter/build facts, declared av constraints,
unknown installed av, module origin `not_found`, unavailable mapping, and API
`unavailable` because the child provider is a native extension outside the
collector's normal path. Its real `.pyd` path is retained in captured stderr,
but is not promoted to a statically verified current module origin or native
ABI diagnosis. No av wheel metadata is emitted from the parent interpreter.

**Current Root Cause Chain:** symbol ImportError → requested import failed →
provider/cause unconfirmed; Step 6 `dependency_api` remains low/inconclusive
with no additional causal steps. It does not establish that 16.x lacks the
new symbols, and does not claim newer PyAV removed them. Improvements are
original-project traceback ingestion, explicit provenance, and limitations.
Native API coverage and child-path visibility remain known gaps; positive
controls show no false API/ABI diagnosis or over-correlation here.

### Case #4 — NousResearch/hermes-agent #123185

**Current rating: 🟡 Partial diagnosis; unchanged rating.** External Python
3.13.1 still differs from the fixture's project venv, generates the existing
WARNING, and its startup still fails to import `pydantic_core._pydantic_core`.
Supplying the original traceback also produces ERROR/exit 1. The ambiguous
two-environment fixture remains ambiguous and now has an inconclusive
assessment. Matched Python 3.12.14 startup still succeeds with no findings.

External-run evidence retains unknown installed provider and module origin
`not_found`; the child guard's injected venv is not treated as the parent
interpreter's installed environment. Matched-run evidence correctly reads
`pydantic_core==2.27.2` from its own METADATA and keeps the declaration separate.
Its WHEEL says `cp312-cp312-win_amd64`. The build has no SOABI-derived native ABI,
so `tag_check=unknown`, not mismatch or available runtime compatibility.
Startup success remains unverified for the whole project.

**Current Root Cause Chains:** local venv observed → current interpreter identity
differs; separately, missing native-submodule exception → import incomplete →
underlying cause unconfirmed. Step 6 `interpreter_environment` is low/inconclusive
with no added causal steps. It does not prove the guard caused foreign-ABI
loading. Matched installed metadata is a real Step 3/4 improvement, but the
ABI cause remains partial. Child-path visibility and conservative missing-build
facts are coverage limitations, not evidence of a wrong ABI verdict.

### Case #5 — ANRAR4/AutoBTD6 #36

**Current rating: ❌ Missed diagnosis; unchanged rating.** Both defaults and
the supplied original CPython 3.13 failure log have no diagnosis, exit 0,
legacy `healthy`, and assessment/CLI inconclusive. The log correctly retains
`unsatisfied_requirement` and `no_matching_distribution` facts for tensorflow.
It contains no wheel filename and no resolved-version fact.

Evidence distinguishes unconstrained declared tensorflow from unknown installed
tensorflow. No locked or resolved version is invented. **Current Root Cause
Chain: none.** Step 6 does not create install-artifact correlation without
related artifacts. The independent historical index evidence of unavailable
CPython 3.13 wheels remains outside this automatic diagnosis.

As a separate control, the original CPython 3.12 successful-download log is
ingested unchanged. Step 5 reads its `tensorflow-2.18.0-cp312-cp312-win_amd64.whl`
and reports tag mismatch against the current 3.13 interpreter, with runtime
compatibility unknown. It produces no ERROR, no installed-version claim, and
no failure correlation. This validates log artifact facts, not the absence of
all historical 3.13 candidates. The two logs are never combined to manufacture
a cause. Historical artifact-availability coverage remains missing; explicit
pip failure without artifact context remains facts-only, as documented.

### Repeated gaps, new observations, and false-positive checks

- **Healthy semantics:** legacy `status=healthy` still appears in no-finding
  defaults and Case #5 failed-install ingestion. Step 1 correctly prevents a
  current CLI/assessment claim of verified health. Consumers reading only the
  legacy field still need the documented compatibility distinction.
- **Child environment visibility (#1–#4):** the collector's interpreter paths
  do not include paths inserted by the independent or startup child. Actual
  stdout/stderr paths are preserved but do not prove current installed ownership.
  Unknown/unavailable is accurate; automatic provider-level depth is limited.
- **New concrete metadata observation:** normal installed build, pip, wheel,
  and editable RepoRescue distributions have `../../Scripts/*.exe` RECORD
  entries. Strict dot-segment validation rejects them, so their otherwise
  readable Name/Version facts are marked unavailable and discovery records only
  `ValueError`. This is reproducible loss of installed-evidence coverage on
  ordinary distributions, recorded for Stabilization compatibility triage;
  it is not evidence that the case target packages were installed. No safety
  boundary was relaxed and no fix was attempted. Severity beyond this observed
  coverage loss is not established by these five cases.
- **Large/nested declaration coverage:** Kotaemon's real lockfile exceeds the
  documented limit; root workspace references do not expose nested Gradio/Hub
  constraints. These are bounded-source limitations, not permission to increase
  scanning or pretend a lock was used by the installer.
- **Native and wheel coverage:** native API exports are not established from
  plain-source AST; the matched 3.12 build yields unknown ABI tag compatibility;
  failed-install logs without candidate artifacts cannot prove historical
  candidate absence. These are explicit coverage limits for Stabilization
  assessment, not new roadmap commitments.
- **Correlation depth:** #2/#3/#4 produce only low-confidence inconclusive
  context, with empty correlation root-cause chains. #1/#5 have none. Step 6
  structures uncertainty but adds no proven underlying cause in this sample.
- **False positives/regressions:** av 17/18 and matched Hermes controls still
  succeed without diagnosis; the separate successful-download log creates no
  installation ERROR; no version difference alone becomes incompatibility.
  No misdiagnosis, over-correlation, or rating regression was observed. No new
  verified P1/P2 correctness/security blocker was demonstrated; the metadata
  coverage issue is retained for Stabilization triage, not silently fixed.

### Outcome and verification

There is **1 rating improvement, 4 unchanged ratings, and 0 regressions**:
4 Partial and 1 Missed, with no Correct or Misdiagnosis rating. Steps 1/2 make
real captured failures usable and prevent misleading verification claims;
Steps 3–5 add bounded factual context where available. This sample does **not**
yet demonstrate stable resolution of these small real-world root causes.
Detection improved, but the confirmed SDK migration, Hub removal, PyAV version
floor, foreign native ABI, and historical TensorFlow artifact gap are not
automatically established. This observation does not enter Phase 2.

- `python -m unittest discover`: **858 tests, OK; 857 passed, 1 skipped**.
- `git diff --check`: passed after the documentation append.
- Product code, fixtures, versions, tags, and history are unchanged. Only this
  document is appended in the repository; no commit or push was performed.
