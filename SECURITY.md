# Security

By default, RepoRescue does not execute project code. It runs only diagnostic
commands in its strict existing allowlist. RepoRescue does not support arbitrary
shell command execution. Do not extend the allowlist with untrusted commands or
trust a plugin's SAFE label as authorization. Repair previews are not execution
permissions.

With the explicit `--run-startup-probe` option, the user chooses to run a
controlled startup probe. The only supported entrypoint is a `main.py` directly
in the project root. The probe uses the current absolute Python interpreter,
`shell=False`, CAUTION risk, and a timeout. Stdout and stderr capture are
bounded. Project code may itself produce side effects.

The interpreter must be trusted. There is no generic secret masking, hard capture
memory bound, or process-tree isolation. Review report paths and captured output
before sharing.

Installed third-party Packs are executable Python code: explicitly discovering
their entry points imports code and invokes factories. Only install/load Packs
from trusted sources. Metadata and protocol validation are not sandboxing.
The public SDK does not expose Core executors, shell helpers or file writers,
but third-party code can import Python libraries and cause its own side effects.
Default CLI/workflow do not discover or load third-party Packs.

Use a private GitHub security advisory when the repository offers that channel.
Otherwise arrange a private reporting channel with the maintainers before sharing
sensitive details. Never post API keys, passwords, tokens, or private user data in
public issues. No private reporting address is configured in this source release.
