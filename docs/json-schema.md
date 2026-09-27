# JSON contract: schema 0.2

Schema version `0.2` is independent of package version `0.3.0a1`.
Existing fields are retained; new fields are additive. Accept unknown extra fields.

| Top-level field | Meaning |
| --- | --- |
| schema_version | Contract version string |
| generated_at | ISO 8601 generation timestamp |
| tool | name (`repo-rescue`) and package version |
| status | Legacy findings summary: healthy, issues_detected, or unknown; not project verification |
| capabilities | Fixed tool capabilities, not per-project results |
| project | ProjectInfo snapshot |
| detection | level (likely/unknown) and matched_files |
| environment | EnvironmentInfo snapshot, including launch verification |
| diagnostics | DiagnosisResult objects |
| evidence | Referenced structured observations |
| assessment | Additive, optional run assessment; authoritative coverage and verification semantics when present |

Status priority: ERROR/CRITICAL means issues_detected; otherwise unknown detection
means unknown; otherwise WARNING means issues_detected; otherwise healthy.
Healthy refers only to limited current checks. INFO safety notices alone are not
project faults. CLI exit 0 allows warnings/unknown, exit 1 denotes ERROR/CRITICAL,
and exit 2 denotes tool/usage/output errors with no fabricated diagnostic report.
Recoverable supplied-log ingestion failures are limitations, not exit-2 errors.

### Run assessment — status semantics

The legacy `status` algorithm and values above remain unchanged for consumers
that already use schema 0.2. It is a findings summary only: `healthy` never
establishes project health or completed checks. New consumers must consult
`assessment` when present; CLI uses this same assessment instead of presenting
the legacy label as a health conclusion. Old reports without this object do not
establish verification either.

`assessment` adds these fields:

- `outcome`: `issues_detected` for any ERROR/CRITICAL/WARNING diagnosis;
  otherwise `inconclusive` when a recorded check is incomplete, ambiguous or
  unavailable; otherwise `no_issues_detected` within the supported checks.
  Known findings take priority, while limitations remain visible separately.
- `verification`: currently always `unverified`. This tool does not execute
  project-wide verification. Neither Python `--version` nor startup exit 0
  establishes project health.
- `coverage`: `incomplete` when limitations are recorded, otherwise `limited`.
  There is no full-coverage or verified-health claim in either case.
- `startup_probe`: the observed `status` from startup Evidence, or
  `not_observed` when absent, plus its run-local `evidence_refs` array. A success
  label is accepted only when `executed` is true and `exit_code` is 0; otherwise
  assessment uses `outcome_not_confirmed`. The original Evidence is retained.
- `limitations`: objects with `check`, `reason` and `evidence_refs` arrays.
  Reasons retain the observed status, or use `not_observed`, `not_verified`
  or `outcome_not_confirmed` when a result cannot be established. References
  can be empty when no corresponding Evidence exists.
  Extension failures use `check: extension`, retain the known failure status as
  `reason`, and add `extension_id` and `stage` (null for compatibility failures).
  They have no fabricated Evidence references or raw exception text.

Incomplete checks include unknown project detection, unverified Python launch,
unrun/unsupported/unavailable/rejected/timed-out startup, ambiguous/unavailable
local interpreter evidence, and unsupported/unreadable/invalid Python requirement
metadata, and known Extension/Pack stage or compatibility failures. A startup
result without a confirmed execution and exit code also
remains incomplete. Missing `requires-python` is not invented as a restriction.
These notices do not create new failure diagnoses.

| Scenario without other findings | Legacy status | Assessment outcome | Coverage | Project verification |
| --- | --- | --- | --- | --- |
| Default scan; startup not run | healthy | inconclusive | incomplete | unverified |
| Local interpreter ambiguous | healthy | inconclusive | incomplete | unverified |
| Confirmed startup exit 0; no recorded limitations | healthy | no_issues_detected | limited | unverified |
| Startup timeout INFO | healthy | inconclusive | incomplete | unverified |
| ERROR/CRITICAL diagnosis | issues_detected | issues_detected | limited or incomplete | unverified |

Legacy unknown-detection priority is also retained: it can yield `unknown` with
a WARNING, while assessment reports the known issue and incomplete coverage.
CLI exit codes remain unchanged; exit 0 is not a project verification result.
This is an optional-field addition with unchanged existing fields, types, values
and meanings, so schema stays 0.2. Package and schema versions remain independent.

Capabilities are diagnosis, root_cause_analysis, repair_preview, verification_plan
(true), and repair_execution, verification_execution, rollback (false).

Diagnosis retains diagnosis_id, problem, category, severity, evidence_refs,
probable_causes, confidence, recommended_actions, source, root_cause_chain,
repair_plan. Confidence is a rule indicator, not statistical probability.
Probable causes are possibilities, not established facts.

- RootCauseStep: id, title, explanation, evidence_refs. Every generated step
  references an existing Evidence. The chain can be short when evidence is limited.
- RepairPlan: id, diagnosis_id, summary, risk, actions, verification_steps,
  execution_status (always `not_executed`). At least one verification step is required.
- RepairAction: id, description, affected_paths, reversible, requires_confirmation.
  These descriptive fields are not executable operations or permission grants.
- VerificationStep: id, description, type (command/file_check/manual), status
  (always `not_run`). These are future checks, not the already completed version probe.
- Risk: LOW/MEDIUM/HIGH. Existing manual-review previews use LOW. Python version
  mismatch previews use MEDIUM because selecting or manually provisioning an
  environment can affect dependencies. No environment operation is executed.
- Safety-policy notices have no repair plan (JSON null).

Evidence IDs are local to a diagnostic run. Execution references correspond to the
execution results available through WorkflowResult; no cross-run identity is promised.
Inspection evidence records the pre-launch snapshot; the final environment can
reflect a subsequently verified launch. Read captured observations accordingly.

Serialization uses only the standard library: dataclasses become objects, Paths
and Enum values become strings, datetimes use ISO 8601, tuples become arrays, and
None becomes null. Reports include local paths and captured output; generic secret
masking is not implemented. Review before sharing. README's excerpt omits fields;
the actual report contains the full serialized model.

Python requirement Evidence uses kind python_requirement, source pyproject.toml,
and optional metadata (an object, empty for previous evidence kinds):

- declared_python_requirement: original requires-python string, or null when unavailable.
- current_python_version: runtime version, preserving prerelease suffixes.
- status: compatible / incompatible / unsupported_requirement /
  unsupported_current_version / invalid_metadata / unreadable_metadata /
  metadata_too_large.

Only incompatible triggers the ERROR python_version rule. Unsupported/read/format
statuses are evidence notices, not project failure diagnoses. An overall healthy
status does not establish compatibility when this check is unsupported;
assessment records incomplete coverage and an inconclusive outcome unless a
diagnosis takes priority. Consult the evidence. Schema remains 0.2.

Project-local interpreter Evidence uses kind local_python_environment and source
python_plugin. Metadata contains:

- current_python_executable: current executable path or null.
- detected_local_environment: .venv/venv, an array for multiple candidates, or null.
- detected_interpreter_path: candidate path, an array for multiple candidates, or null.
- status: matched / different / ambiguous / none. An unavailable status records
  filesystem/normalization failures or an unknown current executable.

Only different produces a WARNING python_environment diagnosis, with its own
evidence-linked chain and LOW preview. Ambiguous/unavailable are assessment limitations;
matched/none are positive or neutral evidence. Existing Python version mismatch
diagnoses remain independent. Schema remains 0.2.

Python import failure Evidence uses kind `python_import_failure` and source
`captured_execution_output`. It is emitted only for an unsuccessful captured
execution and one of two explicit error-line forms. Shared metadata is:

- exception_type: `ModuleNotFoundError` or `ImportError`.
- source_stream: `stderr` or `stdout`; stderr takes precedence.
- raw_message: only the selected exception line, not a whole traceback.
- status: `missing_module` or `symbol_import_failure`.

`missing_module` adds only `missing_module`. `symbol_import_failure` adds only
`imported_symbol` and `source_module`. Import names are not mapped to package
distribution names. These additive metadata fields do not change schema 0.2.

Startup Evidence uses kind `startup_probe`, source `python_startup_probe`, and a
run-local ID `project:startup_probe`. Additive metadata includes:

- entrypoint, interpreter, cwd: path strings (interpreter can be null).
- argv: exact executable/argument array or null when unavailable.
- timeout_seconds: observation limit, default 5.
- execution_status: no_supported_entrypoint / unavailable / requires_confirmation /
  rejected / success / failed / timeout.
- exit_code: integer or null; duration_ms: number or null before an attempt.
- stdout_excerpt, stderr_excerpt: at most 4096 characters each.
- timed_out, capture_timed_out, terminated, executed, stdout_truncated, stderr_truncated: booleans
  where an executor outcome is available. executed records known launch, not side-effect absence.

Startup stdout/stderr each retain at most 64 KiB of bytes before UTF-8 decoding;
excess is continuously drained and discarded. Truncated flags are true when bytes
or excerpt characters were omitted, or pipe collection could not finish. They do
not change execution_status or establish a startup failure. `timed_out` means the direct
child exceeded its observation window. `capture_timed_out` means output collection
exceeded its separate one-second deadline; it preserves a known exit_code and records
an assessment limitation (`check: startup_capture`, `reason: timeout`). Known nonzero
exit remains an ERROR / issues_detected; exit 0 with incomplete capture is inconclusive.
Both flags may be true for a process timeout followed by capture timeout. The caller
closes its pipe handles before returning without supervising descendants. Schema stays 0.2.

No supported entrypoint and requires_confirmation are not project errors. Nonzero
exit is an ERROR startup symptom, timeout is INFO and not verified success, and
exit 0 is positive evidence only. Startup/import/environment/version diagnoses
remain independent. Repair and verification remain unexecuted plans. Metadata
inspection is READ ONLY; confirmed startup is controlled project execution with
possible project-defined side effects. Schema stays 0.2.

## Supplied traceback and install-log evidence

`--traceback-file PATH` and `--install-log PATH` are mutually exclusive. Either
accepts `-` for stdin. Inputs must be UTF-8 (optional BOM), at most 256 KiB;
files must be regular files rather than directories, symlinks or junctions.
Missing, unreadable, oversized, undecodable, empty or unsupported inputs produce
an ingestion limitation and allow remaining project checks to continue.

The existing Evidence envelope is reused: kind `provided_log`, ID `input:log`,
source `user_file` or `stdin`, location the absolute input path or null for stdin,
and summary identifying externally supplied, unverified observations. Metadata:

- input_type: `traceback` / `install_log`.
- size_bytes: bytes read, including any BOM.
- status: `parsed` / `facts_only`.
- Install logs additionally contain `messages` and `patterns` arrays, retaining
  only recognized lines. Patterns: `no_matching_distribution`,
  `unsatisfied_requirement`, `ignored_python_versions`, `requires_python`.

Tracebacks reuse the existing last-supported-exception-line parser and import
diagnoses. Derived Evidence kind remains `python_import_failure`, but source is
`provided_traceback`, ID `input:log:python_import`, associated_id `input:log`, and
source_stream `user_file` / `stdin`. No ExecutionResult is invented. Existing
captured-execution IDs, gating, source_stream values and startup behavior remain
unchanged. Recognized imports yield ERROR symptoms / issues_detected, without
claiming that the underlying distribution or current runtime cause is known.

Install logs record literal pip messages only; Requires-Python and ignored
versions are not promoted to project constraints or compatibility conclusions.
These facts alone remain inconclusive and produce no speculative diagnosis.
Every supplied log contributes a `provided_log` assessment limitation with its
status and Evidence reference. It cannot verify the current environment, even
alongside successful startup. Existing findings retain precedence.

Unusable inputs instead produce kind `ingestion_limitation` with ID `input:log`,
source `user_file` / `stdin`, location the requested absolute path / null, and
metadata `input_type` plus `status`. Reasons are `missing_file`,
`permission_denied`, `read_error`, `not_regular_file`, `oversized_input`,
`invalid_utf8`, `empty`, or `unrecognized`. This observes a collection limitation,
not a failure in the supplied log: no `provided_log`, import-failure Evidence,
or speculative diagnosis is generated. Raw bodies and operating-system exception
messages are not retained. Assessment limitations use `check: log_ingestion`,
and include reason, source, input_type, requested_path and evidence_refs.
Without independently detected issues, outcome is `inconclusive`, coverage is
`incomplete`, and CLI exits 0. Existing ERROR/CRITICAL diagnoses still exit 1 and
retain `issues_detected`; limitations remain visible. Usage errors, invalid project
paths and internal workflow/output failures still exit 2.

Raw input bodies are discarded after parsing; selected exception/pip lines may
still contain sensitive text. Log commands are never evaluated or executed.
Terminal rendering applies one shared control-character escaping function at the
report boundary, including every derived diagnosis/preview and requested path.
Only line feeds remain active; ESC, other C0, DEL and C1 are displayed as visible
hex escapes. JSON serialization escapes controls and non-ASCII characters while
preserving their decoded values; terminal formatting never alters diagnosis facts.
Input parsing does not access the network, install packages or modify the project.
The usual version probe and explicitly confirmed startup probe remain separate.
New Evidence kinds/metadata and limitation values are additive under schema 0.2;
no existing field is removed or renamed.

## Current interpreter, installed distributions and module origins

Step 3 adds optional Evidence kinds, retaining schema 0.2 and all existing fields
and exit codes. These observations do not change diagnoses or repair previews.
They describe the running RepoRescue interpreter, not necessarily the interpreter
or import search path that produced an externally supplied log or startup output.

- `python_interpreter` (`environment:interpreter`, source `stdlib:sys`): executable,
  full version, prefix, base_prefix, is_venv, platform, machine and pointer_bits.
  This is collected once; it does not launch or switch interpreters.
- `python_module_origin` (`module:<name>`, source `stdlib:importlib.machinery`): module,
  origin, search_locations, module_type (module/package/namespace/builtin/frozen),
  interpreter_ref and trigger_evidence_refs. lookup_scope explicitly identifies
  static stdlib spec lookup. Site-packages roots and matching paths, prefix_paths
  and venv_paths record lexical membership, not symlink ownership or compatibility.
  `available` means a spec was found; `not_found` means this limited lookup found
  none; exceptions yield `unavailable` with only error_type, never raw exception text.
- `python_distribution_mapping` (`mapping:<module>`, source
  `stdlib:filesystem_metadata`): module, top_level_module, candidate_distributions,
  candidate_distribution_refs, interpreter_ref, status and discovery_errors.
  One candidate is `available`, multiple metadata records are `ambiguous`, none
  is `unknown`; incomplete discovery or malformed metadata is `unavailable` unless
  already ambiguous. No candidate is selected automatically. Candidate records
  in distinct directories remain separate even when their Name headers are equal.
- `python_distribution` (`distribution:<run-local index>`, source `stdlib:filesystem_metadata`):
  metadata name, installed_version, metadata_location,
  dist_info_location, interpreter_ref and status. Locations are null when not
  determinable from the actual discovered metadata source. Lookup exceptions yield unavailable;
  partial known facts are retained, without inventing installed versions or paths.

Only explicit `python_import_failure` module names trigger metadata/module lookup,
including startup and supplied traceback symptoms. Dotted names map through their
top-level package; repeated targets and distribution candidates are deduplicated.
With no target, no distribution inventory or module scan occurs.

Metadata discovery directly lists immediate *.dist-info / *.egg-info children of
the current interpreter's explicit sys.path / site-packages directories. Stdlib
email parsing reads each directory's own METADATA / PKG-INFO (or an egg-info file);
Name, Version and metadata path come from that one source. Linked metadata sources
are rejected. RECORD never determines the metadata path. Static mapping prefers
top_level.txt, then infers top-level names from validated installed RECORD entries without
opening package code. RECORD uses strict CSV with three-field rows; RECORD entries reject absolute paths, dot segments, controls and ambiguous path
components. File-list entries are never opened, including paths to other metadata.
Name and Version accept identical duplicate headers, but conflicting duplicates,
malformed headers or missing fields make the observation unavailable; unknown
fields remain null. No distribution-name normalization merges records. Missing,
unreadable or malformed metadata records lower coverage, preserving known candidates.
No packages_distributions(), distribution(), Distribution.discover(), or third-party
find_distributions() is called. ZIP metadata discovery is outside this filesystem
scope; ZIP module specs remain supported by the separate origin lookup.
Reads are bounded to 1 MiB per metadata/header/top-level file and 4 MiB per
RECORD file. SOURCES.txt is not read or used for ownership: it describes source
layout rather than installed module paths. Discovery allows at most 128 distinct search roots,
10,000 immediate entries and 2,048 metadata candidates per directory. Linked
sources/search-root ancestors and exceeded limits degrade to unavailable with
incomplete coverage; over-limit directories contribute no partial candidates.

Ordinary `importlib.util.find_spec()` can import a dotted name's parent and invoke
custom import hooks. To honor the no-execution boundary, the collector directly
uses stdlib builtin/frozen/file/zip spec finders, traversing package search locations
without invoking loaders or importing targets/parents. Custom import hooks and
runtime changes to package `__path__` are outside this static lookup's scope.
No sys.path changes, package installations, network calls or project writes occur.

Unavailable origins/metadata, unknown or ambiguous mapping and limited not-found
lookups contribute assessment limitations and incomplete coverage, not new failure
diagnoses. Existing ERROR/WARNING findings retain precedence. Module mapping does
not prove the distribution supplies the resolved file. Step 4 adds the version-source distinction below. Step 5 adds limited compatibility Evidence below; no runtime compatibility
inference, root-cause correlation or upgrade/downgrade recommendation is implemented. Terminal paths use the existing external-path
presentation and shared control escaping; JSON retains structured original facts.


## Version provenance — v0.4 Phase 1 / Step 4

`python_version_provenance` reuses the Evidence envelope and schema 0.2. Each
run-local `provenance:N` row has `provenance` (declared/locked/resolved/installed),
original `name`, PEP 503 `normalized_name`, literal `value`, `status`, and
`evidence_origin` (field/group/line or metadata path). `source` and `location`
identify the actual file, stdin or current-interpreter metadata. Unknown names
and values are null. An empty declared value means an unconstrained declaration.
Optional `limitation` and `error_type` describe uncertainty without raw exceptions.
No version comparisons or failure diagnoses are added.

- **declared:** PEP 621 project.dependencies and optional-dependencies in
  pyproject.toml; requirements*.txt; setup.cfg options.install_requires and
  options.extras_require. Requirements retain raw_requirement, extras and marker.
  A limited literal specifier grammar is accepted; markers are preserved but not
  evaluated (`unknown`). Dynamic dependencies, executable setup.py, Poetry's
  declaration dialect, remote/local references, include directives and hashes
  remain unknown/unavailable. Includes are never followed and setup.py is never
  executed. A requirements `==` pin remains declared, never locked.
- **locked:** uv.lock package name/version (format version 1), poetry.lock package
  name/version (known 1.0/1.1/2.0/2.1 lock-version), and Pipfile.lock default/develop
  exact `==` versions. Local/editable uv packages and Poetry file/directory/git
  sources do not supply supported registry pins. Unknown versions/structures and
  non-exact Pipfile values degrade; lock entries are observations, not proof of
  applicability to the current interpreter, platform or selected extras.
- **resolved:** only explicit pip dry-run `Would install name-version ...` lines
  from the existing user-supplied install-log input. The whole line must contain
  supported package/version tokens. Download candidates, Collecting lines,
  successful installation summaries and commands do not supply resolved facts.
  Lines over 4096 characters and collections over 2048 rows yield no resolved
  rows. `source_evidence_ref` links to the supplied log, `evidence_origin` to its
  line; raw_message retains only the selected literal token. The scope is
  `supplied_log_only`, never the current environment.
- **installed:** projection of Step 3 `python_distribution` observations, with
  `source_evidence_ref`, metadata origin and `interpreter_ref`. Scope is
  `current_interpreter_only`. Explicit distribution names from declarations,
  locks and resolved rows also target that existing bounded metadata collector;
  no module-name guess is made. Missing observations are unknown, not a claim
  of absence. `python_distribution_discovery` records incomplete discovery.

Multiple origins and duplicate rows remain distinct. Different known values for
the same normalized name **within one provenance** mark available rows ambiguous;
this expresses alternatives, not incompatibility. Different provenances never
conflict with each other. Names such as foo-bar and foobar remain distinct.
The normalized key never replaces the original name or merges distributions.

Only recognized sources in the existing shallow project snapshot are read:
root and one direct child level, excluding the existing environment/cache paths.
No recursive content scan or arbitrary log discovery occurs. Reads are limited
to 64 sources, 256 KiB per source, and 2048 parsed project rows; exceeded limits,
malformed files and unsupported metadata remain explicit limitations. Links,
junctions, linked ancestors and escaped snapshot paths are refused. Concurrent
path replacement after validation remains a filesystem limitation, as with the
existing collectors. With no explicit dependency or import target, installed
metadata discovery is not run. Empty supported dependency lists add no invented
dependency facts.

Unknown/unavailable/ambiguous provenance and failed discovery contribute
assessment limitations and incomplete coverage. Existing findings retain
priority, exit codes are unchanged, and terminal rendering uses shared control
escaping. No network, installation, third-party import, project execution,
repair recommendation, ABI/wheel inference or cross-evidence correlation is
introduced by this collector.


## API / Wheel / ABI / Platform Evidence — v0.4 Phase 1 / Step 5

These additive Evidence kinds retain schema 0.2, existing diagnoses, previews,
legacy statuses and exit codes. They do not introduce repair recommendations,
package operations, cross-evidence root-cause correlation or runtime validation.
All observations retain their original sources and run-local references.

### Current interpreter build facts

`python_abi_platform` (`environment:abi_platform`, source `stdlib:sysconfig`)
records implementation, python_version, sys_platform, pointer_bits, SOABI,
extension_suffix, platform_tag, platform_tag_overridden, debug_build, gil_disabled, abi_flags and a
conservatively derived native_abi where recognized. `interpreter_ref` links to
Step 3; runtime_scope is current_interpreter_only. Absent/unknown build details
do not become an assumed ABI. Build fields are read only from an already
initialized stdlib sysconfig cache: this collector never initializes it through
lazy imports. A missing cache produces unavailable with known basic interpreter
facts retained. Platform helpers with potentially lazy imports are not invoked;
unknown platform labels remain null. No process, libc probe or binary loader runs.

### WHEEL and supplied artifact facts

`python_wheel` from installed_wheel_metadata reads only the WHEEL file beside a
selected distribution's own METADATA, inside its actual .dist-info directory.
source_evidence_ref identifies that Step 3 distribution; evidence_origin and
location identify WHEEL. The collector accepts Wheel-Version 1.0 and literal
Root-Is-Purelib true/false, retaining raw_tags and expanding compressed tag sets.
Root-Is-Purelib records an installation scheme, not proof that all code is pure
Python. Multiple tags are alternatives; repeated tags remain in raw_tags and
are deduplicated only for checks. Duplicate/conflicting required headers,
missing files, malformed tags, unknown wheel format and exceeded limits remain
unavailable. Unavailable distribution identity keeps the wheel observation
unknown. Different distribution origins are never merged.

Existing supplied install-log ingestion also records explicit Processing,
Downloading, Using cached artifact lines and pip's exact unsupported-wheel
error form. Filename components and tags are parsed from the basename only;
artifact_token retains the supplied path/URL as text, evidence_origin the log
line, and source_evidence_ref the log. No path, URL, archive or downloaded
candidate is opened. observation distinguishes artifact_mention from
logged_rejection. Neither becomes resolved/installed provenance, and a logged
rejection is not attributed to the current interpreter. Malformed filenames
remain unknown; unrelated commands and ambiguous prose yield no artifact row.

`tag_checks` lists each expanded tag's tag_match/tag_mismatch/unknown result;
`tag_check` is tag_match if any alternative matches, unknown if any remaining
alternative is unsupported, otherwise tag_mismatch. This is a deliberately
limited tag check against the current interpreter. runtime_evidence_ref points
to environment:abi_platform and tag_check_runtime_scope is
current_interpreter_only, even when the filename came from a supplied log.
compatibility_scope is metadata_tags_only. runtime_compatibility always stays
unknown: matching labels cannot validate binaries, system libraries, package
code, APIs, or the environment of an earlier log.

Supported checks cover generic py major/minor none tags, CPython exact minor
none/native ABI, normal CPython abi3 lower bounds and Windows platform tags.
CPython 3.8+ debug builds also admit the corresponding release ABI. Free-threaded
abi3, unrecognized ABI/runtime combinations, same-OS manylinux/musllinux libc
requirements and macOS deployment targets remain unknown. Exact Linux basic
platform labels can match only with known consistent process bitness and no
cross-build platform override; otherwise they remain unknown. Known Windows/Linux/macOS family differences can
exclude a tag. This is not a complete replacement for an installer's tag engine.
Native ABI combined with platform any is not assumed universally eligible.

### Static API syntax

`python_api` (source stdlib:ast) is gated by an explicit symbol-import failure.
source_evidence_ref identifies the failure; module_origin_ref identifies the
current Step 3 module observation; evidence_origin/location identify the actual
plain .py source when available. requested_symbol and module retain the request.
Only bounded source bytes are parsed. Source encoding cookies use a restricted
UTF-8/ASCII/Latin-1 alias set; unknown codecs are unavailable without calling
the extensible codec registry or importing codec modules. A UTF-8 BOM conflicting
with a non-UTF-8 cookie is unavailable.
direct_syntax_bindings records top-level def/class/import/simple assignment or
annotation syntax and line numbers. literal_all_observations records only
literal list/tuple __all__ membership and lines. No expression, decorator,
annotation, import or project code is evaluated.

symbol_observation is direct_syntax_seen or not_seen_in_direct_syntax. Neither
asserts a runtime export: conditional code, wildcard imports, __getattr__,
mutation, re-exports, deletes and execution order are not resolved. An available
status means the syntax was read, not that the API exists or is compatible.
api_compatibility always stays unknown. Binary/builtin/frozen/namespace/ZIP
origins without a proven plain source, malformed/oversized source and read
failures become unavailable, with error_type only. Static API observations
always contribute a static_syntax_only assessment limitation.

### Collection and reporting boundary

No recursive scan or new input option is added. Only existing selected
distributions, explicit requested symbols and supplied artifact logs trigger
this collector; no target means no ABI probe or source read. Source reads reject
relative/parent-traversing paths, symlinks, junctions, linked ancestors and
non-regular files; each read is capped at 256 KiB. Validation is not an atomic
filesystem snapshot: concurrent path replacement remains a limitation of these
and the existing read-only collectors.

Limits: 256 expanded tags, 1024 characters per raw tag/filename, 10000 AST nodes,
128 combined distribution/API/log-artifact targets, 4096 characters per selected
log line, and 128 parsed artifact mentions per supplied log. Exceeded budgets
are visible through python_compatibility_collection or unknown/unavailable
source facts; no unbounded directory/archive or binary inspection occurs.
Unknown/unavailable observations and unknown/mismatched tag checks contribute
assessment limitations, never failure diagnoses. Existing ERROR findings retain
issues_detected. Shared terminal escaping and JSON serialization remain intact.

Specification references: [PyPA wheel format](https://packaging.python.org/en/latest/specifications/binary-distribution-format/),
[PyPA compatibility tags](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/),
[CPython debug builds](https://docs.python.org/3/using/configure.html#python-debug-build).


## Step 6: additive correlation contract (schema 0.2)

DiagnosisResult gains optional `correlations` (default empty). Each result has
`id`, `diagnosis_type`, `title`, `explanation`, discrete `confidence`,
`evidence_refs`, `root_cause_chain`, `status`, and `limitations`. RootCauseStep
retains all prior fields and adds optional `parent_id` (default null) and
`relationship` (`observed`, `supports`, `context`; default observed). Existing
diagnosis confidence/severity/plans and fields retain their meaning.
Correlation observations are derived Diagnosis content, never new raw Evidence.

Core requires every correlation and step ref to identify one unique existing
Evidence ID. Duplicate IDs are excluded, including identical duplicate payloads.
Steps have distinct IDs, each parent must be the preceding step of that local
chain, and references must be included in the containing correlation. Thus no
cycles, forward/dangling refs or duplicated appended steps can pass acceptance.
Original diagnosis refs must be contained in each accepted correlation. Core
checks optional hook output against validated enrichment, rejecting removal or
replacement of existing findings, confidence/severity changes and new repairs.

Results/refs are sorted by stable IDs; evidence or proposal order does not rank
candidates. Duplicate proposals are rejected; multiple origins/mappings/runtime
facts are ambiguous. Multiple distribution candidates are never selected.
Conflicting values within a provenance category are ambiguous; differences
between declared/locked/resolved/installed categories alone are context, never
a fault. Unavailable provenance remains inconclusive and all source refs are kept.

Confidence is a rule support indicator, not probability: `medium` means all
premises for a bounded observation/context relationship are present; `low` means
insufficient or conflicting premises. No current rule emits `high` or `correlated`
because Steps 1–5 do not prove environment intention, runtime symbol absence or
native binary ABI causation. `partial`, `inconclusive` and `ambiguous` plus explicit
limitations are first-class output. Existing ERROR findings remain issues_detected.
Historical installation logs plus related artifact observations yield INFO context,
not an ERROR or requires-python diagnosis; no artifact observations keeps Step 2
facts-only behavior. Observed wheel exclusions refer only to that finite set and
the current interpreter, not all available artifacts or the log's runtime.

Bounds: 8192 Evidence records, 256 diagnoses, 8 accepted correlations per diagnosis,
16 steps per correlation, 1024 characters per indexed Evidence ID, 4096 characters
per lexical path or classified install message, and 128 classified install
messages. Exceeded input budgets preserve original findings without correlation.
No additional source scan occurs. Lexical paths reject relative paths, parent
traversal and controls; symlink identity/loaded-module membership remain unproven.
Terminal output labels relationships/status/limitations and renders each derived
step separately; JSON carries the same chain and refs. No schema/version/exit-code
bump, new CLI flag, repair executor or later Phase capability is introduced.


Declared optional-correlation provider failure/unavailability/incompatibility
preserves the completed diagnosis/evidence batch and records a low/inconclusive
correlation limitation; provider exception text is not exposed. Unexpected
exception policy remains unchanged. More than 128 classified install messages or any non-string/oversized
classified message (over 4096 characters) preserves Step 2 log facts without adding artifact correlation, rather than
silently selecting a prefix that could hide conflicting evidence.
