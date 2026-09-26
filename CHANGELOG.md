# Changelog

## 0.3.0a1

### Added

- Extension contracts, `EXTENSION_API_VERSION = "1"`, `Capability`, `PackKind`
  and `ExtensionMetadata`, with a built-in Extension Pipeline and Pack architecture.
- Public `agent_doctor.sdk`, an official synthetic example Pack, and explicit
  `PackRegistry` registration.
- Controlled discovery of installed Pack factories through `reporescue.packs`.
- Windows GitHub Actions tests and CLI/SDK smoke checks for Python 3.12 and 3.13.

### Changed

- Python capabilities now flow through the built-in Extension Pipeline.
  Packs own domain knowledge and planning; Core retains orchestration, safety,
  consent and execution authority.

### Fixed

- Windows long-path / 8.3 short-path interpreter identity and candidate
  deduplication, preserving ambiguity between genuinely different environments.
- Rejected factory results returning Pack classes instead of valid instances.
- Prevented third-party exception text from leaking into pipeline failure results.

### Security / Safety

- Atomic Pack registration, isolated ordinary discovery failures and declared
  stage failures, and deterministic stage results without partial failed batches.
- Public SDK does not grant Core executor authority. Third-party Packs are
  executable Python code and require trust; validation is not sandboxing.
- Added adversarial Pack hardening and privilege-boundary regression tests.

### Limitations

- Extension API v1 is experimental; Python remains the only real built-in
  language Pack. CLI does not automatically load discovered Packs.
- No marketplace, installer, enable/disable management, sandbox or process isolation.
- Repair and verification remain planning-only. JSON schema stays `0.2` and
  runtime dependencies remain empty.

See [release notes](docs/releases/v0.3.0-alpha.1.md) for compatibility and trust boundaries.

## 0.2.0a2

- Added Python requirement compatibility checks, local interpreter mismatch
  warnings, and missing-module / limited symbol-import failure diagnosis.
- Added explicit opt-in startup probes, timeout observations, and bounded output
  capture, backed by deterministic failure fixtures.
- Improved terminal reports and reduced known local path exposure.
- Added bilingual README documentation, an automated demo GIF, updated security
  policy, and complete source-distribution demo materials.
- Kept JSON schema at `0.2` and runtime dependencies empty.

See [release notes](docs/releases/v0.2.0-alpha.2.md) for safety and limitations.

## 0.2.0a1

- Initial public alpha: Python project inspection, evidence-linked diagnostic
  reports, repair previews, verification plans, and terminal / JSON output.
