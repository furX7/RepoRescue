# RepoRescue v0.2.0-alpha Release Review

Review date: 2026-09-26. Windows First; Python 3.13.1 tested.

Historical release-time snapshot. Subsequent source increments, including the
Python requirement check, are documented in README and json-schema.md.

## Scope and metadata

- Distribution: `repo-rescue`; version `0.2.0a1` from `agent_doctor.__version__`.
- JSON schema: `0.2`; tool name: `repo-rescue`.
- CLI: `repo-rescue`, with temporary `agent-doctor` compatibility alias.
- Internal package remains `agent_doctor` to avoid unnecessary import migration.
- Repository/Homepage: https://github.com/furX7/RepoRescue
- Issues: https://github.com/furX7/RepoRescue/issues
- MIT license; no runtime dependencies; Python >=3.12.
- Build backend: setuptools.build_meta, setuptools >=77 for SPDX license metadata.

## Regression verification

146 unittest cases pass, including the original 129 cases and focused contract and
branding checks. Intentional schema/branding output assertions were updated;
diagnostic conditions, allowlist and process permissions remain unchanged.

## Release verification results

- Successfully built `repo_rescue-0.2.0a1-py3-none-any.whl` and
  `repo_rescue-0.2.0a1.tar.gz` using only the project's isolated build environment.
  The wheel was built from the generated sdist.
- Wheel metadata reports Name repo-rescue, Version 0.2.0a1, MIT, Python >=3.12,
  correct repository URLs, no runtime dependencies, and both CLI entry points.
- Wheel contains only nine agent_doctor source files and six metadata/license files.
  Source distribution also contains public docs and tests. No venv, cache, IDE,
  build/dist directories, or local user data are packaged.
- A clean temporary venv installed the wheel with --no-index --no-deps. Imports
  came from that environment's site-packages, outside the checkout.
- Both CLI entry points passed help/version and real Python-project diagnosis.
  The canonical CLI also passed JSON output, unknown-project preview, no-overwrite,
  and invalid-path checks. JSON tool.name is repo-rescue with schema 0.2.
- Target file lists, bytes, and nanosecond modification times stayed unchanged;
  fixture code was not executed. Repair/verification plans stayed not_executed/not_run.
- Pattern scans of 28 distributable source/doc/test files and both archives found
  no credentials, private email addresses, or real personal machine paths.
  Automated pattern checks are not a security audit.
- Temporary installation environments were removed after verification. Local
  ignored .venv/build/dist/egg-info artifacts are not source-upload contents.

No release or branding blocker was found within the documented alpha scope.

## Boundaries

READ ONLY target handling; only the current interpreter's `--version` can execute.
Repair previews remain not_executed; verification plans remain not_run. No repair,
rollback, LLM, new language, GUI, MCP, or new diagnosis rules are implemented.
Known limits are documented in README and SECURITY.md. No repository initialization,
commit, tag, GitHub upload, or PyPI publication is part of this review.
