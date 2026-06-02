# Security Policy

## Supported Versions

Security fixes land on `main` and in the latest tagged release. Use the latest
release unless you have a specific compatibility reason.

## Scope

`agent-relay` is a shared-file coordination tool for interactive coding agents.
The supported threat model is documented in
[`docs/threat-model.md`](docs/threat-model.md). Peer-authored artifacts are
treated as untrusted input; parser failures, unsafe artifact handling,
mis-synchronization, metadata leaks, and privilege-boundary mistakes are in
scope.

Same-user local compromise is out of scope by design: a process running as the
same OS user can already read and write the same workspace files.

## Reporting a Vulnerability

Report security issues privately through GitHub Security Advisories for
`sean2077/agent-ledger` when available. If advisory access is unavailable,
contact the repository owner through GitHub and avoid publishing exploit details
until a fix or mitigation is available.

Useful reports include:

- affected version or commit
- exact command sequence or artifact shape needed to reproduce
- expected behavior and observed behavior
- impact assessment, including whether the issue crosses the same-user boundary
- any local filesystem, sync, or platform assumptions involved

Non-security bugs and usability issues can be filed through the normal issue
workflow or recorded locally with `relay issue add` during relay development.
