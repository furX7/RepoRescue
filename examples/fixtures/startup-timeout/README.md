# Startup Timeout Fixture

Purpose: sleeps 10 seconds, longer than the 5-second startup observation window.

Expected after confirmed execution: timeout evidence and startup INFO. This does
not establish startup failure, a deadlock, or successful readiness.

Safety: standard-library sleep only; no network, file writes, or subprocesses.
RepoRescue stops the direct child after the observation window.

Run: `repo-rescue examples/fixtures/startup-timeout` only inspects and proposes.
After source review, use the explicitly confirmed API shown in the parent README,
substituting this fixture path. There is no CLI confirmation flag.
