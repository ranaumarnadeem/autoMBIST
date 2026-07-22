# The autoMBIST Documentation

[autoMBIST](https://github.com/ranaumarnadeem/autoMBIST) is an open-source,
[OpenRAM](https://github.com/VLSIDA/OpenRAM)-integrated Memory Built-In Self-Test
(MBIST) generator, with built-in redundancy analysis (BIRA) and self-repair
(BISR), plus a programmable march-algorithm and functional-fault-model research
platform. It hardens to a real sky130 GDS through the open
[LibreLane](https://librelane.org) flow.

autoMBIST is:

- **Two tools in one** — a wrapper/RTL **generator** that MBIST-tests a real
  memory macro, and an independent **research platform** for designing and
  grading march algorithms against a 19-primitive functional fault model, with
  no memory macro required.

- **Repair-aware** — an optional redundancy layer wraps a spare-augmented
  OpenRAM macro with a 2D BIRA solver and an autonomous on-chip self-repair FSM
  that runs analyze → decide → verify with no tester involved.

- **Proven to physical closure** — a realistic three-memory sky130 subsystem,
  self-repair included, hardens clean in LibreLane 3.0.5: zero detailed-routing
  violations, LVS-clean including power.

- **Reproducible** — simulation and coverage-gated CI run inside a Nix flake
  (pinned Icarus/Verilator/Yosys/cocotb) that also puts the CLI on `PATH` with
  no separate install step; Apache-2.0 licensed.

Follow the navigation below (or the sidebar) to get started.

```{toctree}
:hidden:
:maxdepth: 2

introduction
quickstart
installation
example
flow
configuration
librelane
challenges
roadmap
```
