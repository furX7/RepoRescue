# Extension SDK foundation

## Status and API version

`agent_doctor.sdk` is the v0.3 foundation public extension surface.
`EXTENSION_API_VERSION = "1"`; API v1 is **experimental during v0.3**, not a
promise of permanent compatibility. The package version is `0.3.0a1` and
the report schema remains `0.2`. These versions describe different contracts.

Default workflow/CLI use built-in Packs only. Hosts can explicitly discover
installed Pack entry points through the SDK; automatic CLI discovery is not
enabled. The official example has no installed entry point and runs only when
explicitly supplied to the registry/pipeline.

## Pack and Extension

An Extension contract describes a capability. A Pack groups the implementations
of those contracts for a technical domain. Core owns stage ordering, data models,
compatibility checks, safety policy, consent and execution. A Pack does not need
a language-specific workflow or a second runtime.

`PackKind` contains LANGUAGE, TOOLCHAIN, FRAMEWORK, ENVIRONMENT and INTEGRATION.
The only real built-in is Python's LANGUAGE Pack. Other language/framework
implementations are planned; the example is synthetic and adds no language support.

## Public imports

Authors should import from `agent_doctor.sdk`, rather than internal modules.
The facade re-exports the original types; it does not copy or wrap the models.
Its explicit `__all__` contains only these names:

| Purpose | Public names |
|---|---|
| Metadata | EXTENSION_API_VERSION, Capability, PackKind, ExtensionMetadata |
| Contracts | Extension, Detector, EvidenceProvider, DiagnosisRule, RepairPlanner, Verifier |
| Observations and findings | ProjectInfo, DetectionResult, EnvironmentInfo, Evidence, DiagnosisResult, RootCauseStep |
| Planning | RepairPlan, RepairAction, VerificationStep |
| Captured outcomes / descriptions | ExecutionResult, CommandProposal |
| Author checks | validate_pack, validate_pack_id |
| Declared Pack outcomes | ExtensionUnavailable, ExtensionIncompatible, ExtensionFailure |
| Explicit registration | PackRegistry |
| Installed Pack discovery | PACK_ENTRY_POINT_GROUP, discover_installed_packs, DiscoveryResult, DiscoveryFailure |

`ExecutionResult` appears in the diagnosis signature, and its `command` field
uses `CommandProposal`. Both describe data; neither has execution methods.
`RootCauseStep` lets authors express optional evidence-backed finding chains.
The compatibility checker and pipeline are Core orchestration APIs, so they
are not exported by this minimal author facade.

## Official minimal Pack

The complete, typed example is
[examples/extensions/example_language_pack.py](../examples/extensions/example_language_pack.py).
Its imports and metadata start with:

```python
from agent_doctor.sdk import (
    EXTENSION_API_VERSION, Capability, PackKind, ExtensionMetadata,
    ProjectInfo, DetectionResult, EnvironmentInfo, Evidence, DiagnosisResult,
    ExecutionResult, RepairPlan, RepairAction, VerificationStep,
)

# ExampleLanguagePack.metadata returns:
metadata = ExtensionMetadata(
    id="example.language", name="Example Language Pack", version="0.1",
    api_version=EXTENSION_API_VERSION, kind=PackKind.LANGUAGE,
    capabilities=frozenset(Capability),
    supported_platforms=("windows",), required_tools=(),
)
```

The file implements each method directly; no base class or builder is needed.
The [demo directory](../examples/extension-demo/README.md) contains only a marker
and README and is labelled **SDK example only**, separate from failure fixtures.
To exercise explicit pipeline integration from a source checkout:

```powershell
python -m unittest tests.test_extension_sdk
```

The example and demo are source-distribution material, not installed wheel
runtime modules. The normal CLI does not load them.

## Explicit registration

This is **explicit programmatic registration**. Default CLI does **not**
automatically discover third-party Packs. Callers import and instantiate their
own Pack, then submit that object through the public SDK. For a source checkout:

```python
from agent_doctor.sdk import PackRegistry
from examples.extensions.example_language_pack import ExampleLanguagePack

registry = PackRegistry.default()  # independent membership; python.core only
registry.register(ExampleLanguagePack())
snapshot = registry.snapshot()    # (Python Pack, Example Pack), in this order
```

`PackRegistry()` starts empty; `PackRegistry((pack_a, pack_b))` registers explicitly
supplied objects in order. `register(pack)` returns None on success and raises
ValueError for invalid SDK metadata, malformed IDs/declarations, missing callable
capabilities or duplicate IDs. A duplicate error includes `Duplicate pack ID`
and the ID. Failure neither reserves the ID nor replaces an existing Pack.
Registration reuses metadata/capability validation and checks tuple/string shape
for platform and tool declarations. Supply a Pack instance, never a class
returned by a factory. It does not execute Pack stages.

`get(pack_id)` returns the supplied object or None. `packs` is a read-only tuple
property, and `snapshot()` captures tuple membership/order. Later registrations
do not change an existing snapshot or prepared pipeline run. Snapshots are
shallow: Pack objects are not copied or sandboxed. Authors must keep metadata/IDs
and capability implementations stable after registration; metadata properties
must have no side effects. There is no global mutable registry or thread-safety
guarantee, unregister operation, lifecycle, priority or persistence system.

Core passes this snapshot as the existing `prepare_extensions` collection;
pipeline also validates incoming collections before compatibility and dispatch.
Its default collection and `PackRegistry.default()` use the same existing
`BUILTIN_EXTENSIONS` source. Core's factory import is fixed and lazy, never based
on caller strings or third-party paths. Default workflow/CLI still use only Python.
No workflow parameter or public execution entry point is added in this step.

Registration validity is distinct from runtime compatibility: API `"2"`, a
different platform or an unavailable required tool can be structurally valid.
Pipeline still reports api_version_mismatch, unsupported_platform or
missing_required_tool and skips that Pack's stages. Registering a Pack grants
no executor, shell, writer, network client or mutable environment handle.

## Installed Pack discovery

An explicit host call queries the current Python environment's installed entry
points in the single fixed group `reporescue.packs`, using the standard library's
[importlib.metadata](https://docs.python.org/3.12/library/importlib.metadata.html#entry-points).
The user must have already installed a trusted distribution. RepoRescue does
not download, install, search for packages or query a marketplace/network service.
It does not scan project/plugin directories or accept file paths/import strings.

A third-party distribution can declare this synthetic package metadata:

```toml
[project.entry-points."reporescue.packs"]
example = "example_package:build_pack"
```

`example_package.build_pack` must be a **no-argument callable** returning one SDK
Pack object. A class with a no-argument constructor is also callable. Direct
Pack objects/module targets are not supported. The official source example is
not registered as an entry point in RepoRescue's own package metadata.

```python
from agent_doctor.sdk import PackRegistry, discover_installed_packs

registry = PackRegistry.default()
result = discover_installed_packs(registry)
snapshot = registry.snapshot()  # supplied explicitly to Core's existing pipeline
```

The caller's registry receives successful candidates through `register()`, using
its existing validation and atomic insertion. There is no global discovery state.
SDK import, default registry creation and normal CLI do not invoke discovery.
API/platform/tool mismatches remain runtime compatibility outcomes, not discovery
failures; they are registered if structurally valid and later filtered by pipeline.

Factories load in case-sensitive `(entry-point name, distribution name, value)`
order; unavailable distribution names sort as an empty string. Duplicate Pack
IDs never replace existing objects. The first candidate in this order wins;
later collisions, including `python.core`, are recorded as skipped failures.
Repeated calls still load factories and reject IDs already registered; discovery
is not a reload or enable/disable mechanism.

`DiscoveryResult` and `DiscoveryFailure` are frozen data:

- `registered_ids`: newly registered/discovered IDs in deterministic order.
- `failures`: all rejected candidates, including duplicate skips.
- `skipped`: failures with status `skipped` (`duplicate_pack_id`).
- `failed`: failures with status `failed` (query_failed, metadata_failed,
  load_failed, invalid_factory, factory_failed or invalid_pack).

Failures include entry-point name, optional distribution name, code and concise
fixed message. Unavailable or unsafe identity labels are omitted. Raw exception
messages, repr and traceback are not returned, preventing exception paths/secrets
from being copied into results. These results never enter schema 0.2 reports.
One candidate's ordinary Exception does not stop other candidates;
KeyboardInterrupt, SystemExit and GeneratorExit propagate. Earlier successful
registrations remain if such a signal interrupts the call.

**Installed third-party Packs are executable Python code.** Entry-point load
imports third-party code and the factory invocation executes it. Use only trusted
installed sources. **RepoRescue metadata validation is NOT a sandbox.** The layer
does not invoke detect/collect/diagnose/plan/build_verification, provide privileged
handles or perform command/network/file-write operations itself; it cannot stop
loaded code from producing its own side effects or output. Automatic CLI discovery,
an installer, marketplace, signatures and process isolation are not implemented.

## Metadata and capabilities

IDs must be stable, machine-readable and match `[a-z0-9][a-z0-9._-]*`.
Name, version and API version must be non-empty; kind must be a `PackKind`.
Use explicit kind for new Packs. Platform/tool names are exact canonical names;
required tools declare availability only, not semantic version ranges.
The example declares Windows under the current compatibility model and needs
no external tools. This does not add platform support to RepoRescue.

`validate_pack(pack)` lets authors check that declared stages have callable
implementations and a valid provider ID. Core also performs this check before
compatibility and dispatch, and rejects duplicate Pack IDs. This does not check
signatures or sandbox implementations; type annotations and behavioral tests
remain necessary. New rule/provider IDs should be stable and namespaced.

| Capability | Method | Example behavior |
|---|---|---|
| DETECT | detect | Check that root `.reporescue-example` is a file; return likely/unknown |
| INSPECT | collect | Return one marker Evidence, or none |
| DIAGNOSE | diagnose | Return one INFO finding from marker evidence, or none |
| PLAN_REPAIR | plan | Describe manual marker review for the example finding |
| VERIFY | build_verification | Return the plan's manual check; never run it |

For an unchanged project and supplied data, results and ordering are deterministic.
The Core scan currently lists Python files/manifests only, so the example checks
one known root marker path without reading file contents or changing the scanner.
`DiagnosisResult.source` is `rule:example.marker-detected`; `diagnosis_id` uses
the same stable rule identity for this single finding. Evidence references link
the finding to `example.language:marker`. Existing Python rule IDs remain unchanged.

Diagnosis returns **the complete evidence batch**, including input observations
needed by its findings, not a delta for Core to append. The example preserves
that batch. RepairPlan must reference its originating diagnosis and include at
least one VerificationStep even before the VERIFY stage. Plans stay
`not_executed`; verification steps stay `not_run`. A Pack without a repair plan
returns no verification steps under the current schema.

## Safety and errors

SDK imports do not expose CommandExecutor, shell/subprocess wrappers, startup
execution, workflow, CLI, Python private diagnostics, report formatting or
environment mutation utilities. Core passes snapshots and captured outcomes,
not privileged service handles. EnvironmentInfo describes the current Python
runtime; future languages should represent their own observations in Evidence.

Packs must not execute commands, install packages, access the network, mutate
inputs, change environment variables, or write/delete project files. Respect
bounded read-only observation and Core policy. Evidence.metadata is only shallow
frozen: authors must leave its dictionary unchanged. Repair planners describe
actions/risk; verifiers describe checks. Core owns any future execution and consent.
Packs cooperate through Core models, not another Pack's private implementation.

Authors may raise `ExtensionUnavailable(pack_id, message)` for missing
observations/prerequisites, `ExtensionIncompatible` for unsupported API/platform,
or `ExtensionFailure` for a failed stage. These are existing contract outcomes
that Core isolates per Pack, preserving earlier completed stages and skipping
later stages. Pipeline failure results retain Pack ID, stage and category with
a fixed message; raw extension exception text is not copied into those results.
Unexpected exceptions remain visible; do not catch process-control
signals or disguise programming errors as successful findings.

These are in-process Python contracts, not a security sandbox: they cannot stop
hostile code importing privileged APIs on its own. Runtime does not automatically
load third-party Packs or grant them command-execution authority. There is no marketplace,
installer, enable/disable, dependency solver, remote/signed manifest or plugin
host. Repair/verification execution, transactions, rollback, GUI and LLM remain
outside this step. No SDK metadata is added to JSON and no CLI flags are added.
