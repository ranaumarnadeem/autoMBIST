# Security Policy

autoMBIST is an open-source EDA research/generator tool: it emits SystemVerilog,
runs simulators, and drives open-source physical-design flows. It does not run
as a network service and handles no user credentials, so its security surface is
small. The most relevant concerns are supply-chain integrity (the published
`autombist` wheel and its dependencies) and safe handling of user-supplied
config/RTL by the generator.

## Supported versions

Security fixes are applied to the latest released version on PyPI (the current
`1.x` line) and to the `main` branch. Older versions are not maintained.

| Version | Supported |
|---------|-----------|
| latest `1.x` (PyPI) | ✅ |
| `main` (git)        | ✅ |
| anything older      | ❌ |

## Reporting a vulnerability

Please report suspected vulnerabilities **privately**, not via a public issue:

- Preferred: open a [GitHub private security advisory](https://github.com/ranaumarnadeem/autoMBIST/security/advisories/new).
- Or email **rana.umar.nadeem21@gmail.com** with a description, affected version,
  and reproduction steps.

You can expect an acknowledgement within a few days. Once a fix is available, a
patched release will be published.
