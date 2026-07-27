# Introduction

## What autoMBIST is

autoMBIST is actually **two independent subsystems** that share one CLI and one
RTL library:

1. **The wrapper generator** — takes a `config.yml` describing a real memory
   (an [OpenRAM](https://github.com/VLSIDA/OpenRAM)-compiled SRAM macro, or any
   macro with the same pin shape) and generates a synthesizable MBIST wrapper
   around it: a march-algorithm controller, a `test_mode` mux between
   functional and test access, optional fault injection for verification, and
   — optionally — a redundancy-repair layer (BIRA/BISR).

2. **The research platform** — a Verilator-driven march-algorithm engine and
   19-primitive functional fault model, used to design and grade march
   algorithms or an arbitrary controller FSM, with **no memory macro required**.
   This is where you'd prototype a new march element or measure a march
   algorithm's coverage against the fault model before ever generating RTL.

The two are deliberately decoupled — see {doc}`flow` for why.

## Redundancy repair (BIRA/BISR)

Real memories have manufacturing defects. Rather than discard a die with one
bad row, spare rows/columns are built into the macro and a repair layer steers
around the defect. autoMBIST implements this as an **external remap**: standard
combinational logic sits between the address mux and a stock, spare-augmented
OpenRAM macro — no repair pins inside the macro itself, so it hardens like any
other logic and needs no special macro views.

- **BIRA** (Built-In Redundancy Analysis) — a 2D solver (`src/autombist/repair/bira.py`)
  that allocates spare rows and columns to failing cells: a must-repair fixed
  point followed by backtracking over the residual, verified against a
  brute-force oracle over 300 random instances.
- **BISR** (Built-In Self-Repair) — two paths:
  - **Tester-driven**: `repair/bisr.py` encodes the BIRA solution into the
    exact register layout the remap RTL expects; a tester loads it.
  - **On-chip, autonomous**: `rtl/onchip_row_repair_analyzer.sv` +
    `rtl/onchip_selfrepair_ctrl.sv` run analyze → decide → verify entirely in
    silicon by asserting `self_repair_start` and holding it until
    `self_repair_done` reads back, no tester involved.

## Proven under a real CPU

Self-repair isn't only tested in isolation. An unmodified RV32I core
(PicoRV32, vendored as-is) boots and runs a real hand-assembled program
entirely through two self-repair-wrapped memories
([`flow/soc/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/soc)):
repair runs to completion at power-on, in its own reset domain, before the CPU
is ever released — then the CPU's own `lw`/`sw` traffic round-trips correctly
through the repaired path. It's demonstrated two ways: against
defect-injectable behavioral memories (proving repair genuinely *fixes* a
defect the CPU would otherwise read wrong), and against the real hardened
OpenRAM sky130 macros (proving it works on the actual macros that close to
GDS). This is the difference between "the repair FSM passes its own testbench"
and "a processor doesn't notice the memory was ever broken."

## Why this exists

Open-source EDA has mature flows for synthesis, place-and-route, and PDK
generation (Yosys, OpenROAD, LibreLane, OpenRAM), but memory test and repair —
MBIST, BIRA, BISR — has stayed closed-source. autoMBIST is an attempt to bring
that piece into the open-source stack, integrated with OpenRAM and provably
closing to GDS through LibreLane on sky130.

## Project status

autoMBIST is a research and demonstration platform, not a drop-in replacement
for a commercial MBIST tool. It's honest about the gap — see {doc}`challenges`
and {doc}`roadmap` for what's built, what's in progress, and what's
out of scope today.
