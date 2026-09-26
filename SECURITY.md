# Security

RepoRescue is a local, read-only diagnostic tool, not an execution sandbox.
The executor allows only the current Python interpreter's exact `--version`
operation, validates the working directory, avoids shell execution, and uses a
timeout. Do not extend its allowlist with untrusted commands or trust a plugin's
SAFE label as authorization. Repair previews are not execution permissions.

The interpreter must be trusted. There is no generic secret masking, hard capture
memory bound, or process-tree isolation. Review report paths and captured output
before sharing.

Use a private GitHub security advisory when the repository offers that channel.
Otherwise arrange a private reporting channel with the maintainers before sharing
sensitive details. Never post API keys, passwords, tokens, or private user data in
public issues. No private reporting address is configured in this source release.
