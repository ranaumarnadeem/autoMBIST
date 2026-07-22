# Installation

## Platform

autoMBIST's Python package runs anywhere Python 3.10+ runs. The RTL simulation
and physical-design tooling it drives (Icarus Verilog, Verilator, cocotb,
Yosys, LibreLane, magic, OpenRAM) is Linux-only — on Windows, run inside **WSL**.
A [Nix flake](https://github.com/ranaumarnadeem/autoMBIST/blob/main/flake.nix)
is provided that pins the entire simulation toolchain (Icarus 13.0, Verilator
5.048, Yosys 0.62, Python 3.11, cocotb 2.x) for reproducible builds; this is
also what CI uses.

## Install (Nix — recommended, matches CI exactly)

```bash
nix develop
```

This is the whole install: the flake pins the exact simulation toolchain
(Icarus 13.0, Verilator 5.048, Yosys 0.62, Python 3.11, cocotb 2.x) and puts
`src/` on `PYTHONPATH`, so the `autombist` CLI works immediately inside the
shell — no separate `pip install` step.

```bash
nix develop --command autombist --help
```

## Manual install (without Nix)

```bash
sudo apt-get install iverilog verilator yosys
python -m pip install -e .
```

This installs the CLI from your local checkout (editable). You're
responsible for keeping `iverilog`/`verilator`/`yosys` versions compatible
yourself — Nix is what pins the tested combination.

## Physical/signoff toolchain (optional)

Only needed for the `harden` / `fix-lef-units` / `macro-signoff` commands and
OpenRAM macro generation — not for the classic generate/simulate/`test`/`algo`
workflow. Provisioned via nix (LibreLane, magic, netgen, klayout, OpenSTA are
all reachable through
`nix run github:librelane/librelane`) and [ciel](https://github.com/fossi-foundation/ciel)
for the sky130 PDK. See {doc}`librelane` for the full recipe.

## Verify the install

```bash
autombist smoke
```

Runs generation, an OpenRAM config parse, and a couple of small fault
simulations end-to-end — the fastest way to confirm your environment is wired
correctly before a real run.

## Legacy PyPI release

A pre-BIRA/BISR release (`1.1.2`, MBIST generation and fault injection only —
none of the redundancy/repair work) remains published on PyPI from before this
project moved to Nix as its packaging path. It isn't kept up to date and isn't
the recommended way to install autoMBIST; see the
[GitHub Releases](https://github.com/ranaumarnadeem/autoMBIST/releases) page
for version history.
