# Security Policy

autoMBIST is an open-source EDA research/generator tool: it emits SystemVerilog,
runs simulators, and drives open-source physical-design flows. It does not run
as a network service and handles no user credentials, so its security surface is
small. The most relevant concerns are supply-chain integrity (the Nix flake and
its pinned dependencies) and safe handling of user-supplied config/RTL by the
generator.

## Supported versions

The `main` branch is the only maintained, supported version — install via the
Nix flake (`nix develop`), which is what CI and this policy both track. A
pre-BIRA/BISR `1.x` release remains published on PyPI from before this project
moved to Nix as its packaging path; it is **not** maintained and does not
receive security fixes.

| Version | Supported |
|---------|-----------|
| `main` (git, via Nix) | ✅ |
| `1.x` (legacy, PyPI)  | ❌ |
| anything else         | ❌ |

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, not via a public issue:

- Preferred: open a [GitHub private security advisory](https://github.com/ranaumarnadeem/autoMBIST/security/advisories/new).
- Or email **rana.umar.nadeem21@gmail.com** with a description, affected version,
  and reproduction steps.

You can expect an acknowledgement within a few days. Once a fix is available, a
patched release will be published.
