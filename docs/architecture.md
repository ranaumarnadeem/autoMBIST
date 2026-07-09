# Architecture

This document explains how autoMBIST is put together internally: the two
independent subsystems it ships, why they're kept separate instead of
unified, the multi-port invariant that makes cross-port coupling faults
meaningful, and how OpenRAM fits into the picture. It assumes you've read the
README and now want the map before reaching for the command reference.

## Two subsystems, one repository

autoMBIST is really two separate tools that share a Python package and a CLI
entry point (`autombist`), but not much else at runtime:

| | **Classic path** | **Algo-shell** |
|---|---|---|
| Entry points | `autombist generate` / `simulate` / `run` | `autombist test` / `autombist algo` |
| Simulator | Icarus Verilog, via cocotb | Verilator 5.x (direct `--binary` build) |
| Subject under test | An actual memory macro (e.g. OpenRAM-generated) wrapped in a generated MBIST harness | A behavioral fault-injectable RAM model (`fault_ram.sv`), independent of any real macro |
| Fault model | Structural array faults: stuck-at (`SA0`/`SA1`), transition (up/down), inter-port coupling | 19 functional fault primitives (stuck-at, transition, write/read-disturb, address-decoder, four coupling classes) |
| Purpose | Production-style test insertion: generate synthesizable MBIST RTL around a specific memory instance | Research/validation: develop and grade march algorithms (or controller FSMs) against a fault model, independent of any specific memory |
| Report builder | `reporting.py` (`build_simulation_report`, JSON schema `1.2.0`) | `algo_reporting.py` (per-campaign / matrix / diagnosis, schema `1.0.0`) |

Both halves live under `src/autombist/`, share the CLI, and both ultimately
render Jinja2-templated SystemVerilog — but the templates, the simulators,
and the fault representations are disjoint. Nothing in the algo-shell touches
`generator.py`'s wrapper/saboteur templates, and nothing in the classic path
touches `fault_ram.sv` or the march engine.

### The classic path (RTL wrapping, cocotb + Icarus)

`generator.py` is the entry point. Given a `config.yml` describing a memory's
port list, address/data widths, and active-low convention, it:

1. Loads and validates the config (`load_config`), normalizing whatever
   `ports:` shape you wrote (legacy flat single-port dict, or a named
   multi-port map) into a canonical `normalized_ports` structure.
2. Renders `wrapper_template.j2` into `<memory_name>_mbist.v` — a module that
   muxes between functional access and MBIST access (`test_mode`) and
   instantiates the selected march algorithm's top module
   (`march_c_top`, `march_1r1w_top`, `march_2rw_top`, ...) wired to the real
   memory instance.
3. If `--test` is passed, also renders `saboteur_template.j2` into
   `<memory_name>_saboteur.v` — a fault-injecting stand-in for the memory
   that applies pre-generated `SA0`/`SA1` masks or transition-fault shadow
   logic (see `fault_gen.py` for the mask generation) — and a per-module
   `Makefile` (`fault_makefile_template.j2`).
4. Copies the algorithm's RTL family (`rtl/march_c/`, `rtl/march_1r1w/`, etc.)
   and shared models into the output directory via `copy_mbist_rtl`.

`autombist simulate` then drives cocotb against that output directory with
`SIM=icarus` (see `runner.py`), and `reporting.py` parses the simulator's
stdout/stderr plus the JUnit XML cocotb emits into a JSON report
(`results.json`/`latest.json`, schema `1.2.0`) with fault coverage, per-site
`fault_details`, and (optionally) FaultFlow controller-structural grading
merged in under `controller_grading`.

This is the path you use to actually MBIST-test a real memory macro — the
"memory under test" is a concrete Verilog module (typically OpenRAM output;
see below), not a parameterized behavioral model.

```
config.yml ──► generator.py ──► wrapper_template.j2 ──► <mem>_mbist.v  ─┐
                            └──► saboteur_template.j2 ──► <mem>_saboteur.v (--test) │
                                                                                     ▼
                                                          cocotb + Icarus Verilog (runner.py)
                                                                                     │
                                                                                     ▼
                                                          reporting.py ──► results.json / report.txt
```

### The algo-shell (fault-model DSL, Verilator)

The algo-shell's job is orthogonal: given *no* real memory macro, develop and
grade a march algorithm's fault coverage against a well-defined functional
fault model. Its pieces:

- **`fault_primitives.py`** — a declarative DSL describing a memory
  functional fault as a `(category, sensitize, effect)` triple. 15 of the 19
  built-in fault types (SA0/SA1, TF0/TF1, WDF0/WDF1, CFIN/CFID/CFST, IRF0/IRF1,
  RDF0/RDF1, DRDF0/DRDF1) are expressible this way; the other four (SOF,
  AF_NOACC, AF_ALIAS, CFDS) are fixed hand-written scaffolding because they
  don't fit the DSL's per-bit-site model (cross-op state, address-decoder
  pre-pass, or a union of several sensitizing conditions). `add_fault_type`
  in the shell lets a researcher register a *new* fault type from this DSL
  without writing SystemVerilog by hand.
- **`fault_ram_gen.py`** (not read in depth here, but the consumer of
  `fault_primitives.py`) renders the DSL registry plus the four fixed types
  into `fault_ram.sv` — the actual behavioral RAM.
- **`algo_engine.py`** is the shared campaign engine: it compiles
  `fault_ram.sv` plus either `march_engine.sv` (algorithm front, driven by a
  `.alg` file) or a generated harness around a researcher's controller FSM
  (FSM front) with Verilator, runs one golden pass and one pass per fault,
  and parses the single `RESULT DETECTED`/`RESULT ESCAPED` line each run
  prints into structured `FaultResult`/`CampaignResult` objects.
- **`algo_shell.py`** is the interactive `cmd.Cmd`-based shell
  (`autombist algo`) tying it together: `set_memory`, `add_algo`, `add_fsm`,
  `add_fault_type`, `add_fault`/`gen_faults`/`load_faults`, `run`,
  `compare_algo`, `write_report`, `write_diagnosis`, `export_tb`. Session
  state is a plain dataclass (`Session`), so the same commands work
  interactively or scripted (`autombist algo --script FILE`).
- **`algo_reporting.py`** builds three independent report families from a
  `CampaignResult`: a per-fault detail table (`write_campaign_report`), a
  multi-algorithm comparison matrix (`write_matrix_report`), and a sparse
  per-(address, bit) diagnosis / fail-bitmap table
  (`write_diagnosis_report`) — each in md/csv/json.
- **`src/autombist/engine/README.md`** is the engine's own reference: exact
  fault-list grammar, the semantics table for all 19 primitives, measured
  MATS+/March C-/March SS coverage against `faults.example.txt`, and the
  full multi-port (`march_engine_mp.sv`) syntax. This document doesn't repeat
  that table — see it for the authoritative per-primitive semantics and
  worked coverage numbers.

```
.alg file ──┐                              ┌──► RESULT DETECTED/ESCAPED (per fault)
            ├─► march_engine.sv ───┐        │
fault list ─┘   (or a researcher's │        │
                 FSM + harness)    ├─► Verilator ──► algo_engine.py (parse) ──► CampaignResult
fault_primitives.py registry ──►   │                                                │
  fault_ram_gen.py ──► fault_ram.sv┘                                                ▼
                                                              algo_reporting.py ──► report / matrix / diagnosis
```

Because the algo-shell never touches a real memory macro, coverage numbers
it produces describe the *algorithm's* fault-detection power against the
functional fault model — useful for choosing or designing a march algorithm
before you ever generate a wrapper for a specific memory.

## Why two subsystems instead of one

It would be natural to ask why autoMBIST doesn't just have one fault engine.
Three reasons keep them separate:

1. **Different simulators for structural reasons, not preference.**
   `fault_ram.sv` uses SystemVerilog queues, `foreach`, and `final` blocks —
   none of which Icarus Verilog supports. The classic path's saboteur, on the
   other hand, is deliberately simple synthesizable-adjacent Verilog that
   cocotb/Icarus can drive cheaply per-config. Merging them would mean either
   dropping Icarus (breaking the classic path's cheap iteration loop) or
   dumbing down the fault model to what Icarus can simulate (breaking the
   algo-shell's fidelity).
2. **Different subjects under test.** The classic path always wraps a
   *specific* memory instance (real port names, real timing) — that's the
   point, it's what you'd tape out. The algo-shell's `fault_ram.sv` is a
   parameterized stand-in whose only job is to expose a clean, well-defined
   fault surface; it's not meant to resemble any particular macro's
   implementation.
3. **Different purposes.** The classic path answers "does my MBIST wrapper
   around *this* memory catch faults, end to end, in a form I could
   synthesize?" The algo-shell answers "how good is this march algorithm (or
   this controller FSM), in principle, against a known fault model?" — a
   question you want answered *before* committing to a specific memory
   wrapper, and one you'll want to re-ask whenever you tweak the algorithm,
   independent of any macro.

The two do share concepts (march algorithms, the same fault-type names,
`MemoryParams`-style address/data widths) and the same repo/CLI, which is
why they live together — but forcing them through one engine would compromise
both.

## The multi-port invariant: one shared core, not two

Both subsystems support 2-port memories, and both are built around the same
non-negotiable rule: **a 2-port memory model must share exactly one
underlying storage/fault-record array across both ports.** Two independently
instantiated single-port cores — one per port — will build and simulate
without error, but silently defeat the entire point of testing a dual-port
memory: cross-port coupling faults (an aggressor write on port 1 disturbing a
victim read on port 0) can only be observed if both ports' accesses resolve
against the *same* underlying state.

Concretely, in `fault_ram_template.sv.j2` the storage declaration

```systemverilog
logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];
```

appears exactly once in the template, unconditionally — not once per port,
and not duplicated inside the `{% if num_ports == 2 %}` branch. When rendered
with `num_ports=2`, the module gains a second port-1 bus (`clk1`/`csb1`/
`web1`/`wmask1`/`addr1`/`din1`/`dout1`) and a second `vp`/`ap` (victim-port/
aggressor-port) field on each fault record, but both port buses index into
that one `mem` array and one shared fault queue (`FQ`). That's what lets a
`CFIN`/`CFID`/`CFST`/`CFDS` fault declare `vport != aport` and actually mean
something: the aggressor's sensitizing condition is evaluated against port
1's live signals while the victim's disturbance is observed through port 0's
access to the same cell.

`openram_shim_mp.sv` is the concrete, deliberately-commented example of
getting this right:

```systemverilog
// CRITICAL: this wraps EXACTLY ONE fault_ram core, rendered with
// num_ports=2 ... -- ONE shared fault-record queue/array underneath both
// port buses. This is what makes cross-port coupling faults meaningful ...
// wrapping two independent fault_ram cores here would silently defeat that
// entirely by giving each port its own memory array with no shared fault
// state.
```

and its body instantiates a single `fault_ram #(...) u_core (...)` with both
port-0 and port-1 signal bundles connected to that one instance — never two
`fault_ram` instances.

The same invariant holds on the classic-path side of a 2-port memory (the
`march-1r1w`/`march-2rw` wrapper/saboteur templates instantiate one memory
module per named port in the *config*, but that's describing the pins of a
single real dual-port macro, not duplicating fault-record state — the
saboteur's fault masks are indexed by address in one shared mask array
regardless of which port accesses them). If you're extending either
subsystem to a new multi-port shape, preserve this: one storage/fault-state
array, N port-facing signal buses reading and writing into it.

## Where OpenRAM fits in

[OpenRAM](https://github.com/VLSIDA/OpenRAM) is the typical source of the
"memory macro" the classic path wraps: it's a memory compiler that generates
a full hardening kit for an SRAM — synthesizable/behavioral Verilog, LEF, and
Liberty timing views — for a given address/data width and port configuration.
`autombist ram-synth` drives OpenRAM directly from an `openram.yml` config,
and `autombist init` scaffolds a starter `config.yml` + `openram.yml` +
`Makefile` for a new project.

Two integration points exist, one per subsystem:

- **Classic path:** the generated wrapper (`wrapper_template.j2` /
  `saboteur_template.j2`) instantiates *your* memory module by the port names
  you declare in `config.yml`'s `ports:` map — so an OpenRAM-generated
  macro's actual pin names (`clk0`/`csb0`/`web0`/`addr0`/`din0`/`dout0`, or
  its 2-port equivalents) are simply what you write into that config; no
  separate shim is required on this path, since the wrapper is generated to
  match whatever pinout you give it.
- **Algo-shell:** `fault_ram.sv`'s own pin names are engine-internal
  (`clk`/`csb`/`web`/`wmask`/`addr`/`din`/`dout`, or the `0`/`1`-suffixed
  2-port equivalents) and don't match OpenRAM's per-byte `wmask` convention
  or exact port order out of the box. `openram_shim.sv` (single-port) and
  `openram_shim_mp.sv` (2-port) exist specifically to drop an
  OpenRAM-shaped port interface in front of the algo-shell's `fault_ram`
  core — expanding OpenRAM's per-byte write mask to the bit-level mask
  `fault_ram` uses internally, so a researcher validating an algorithm can
  swap between "golden run against the real OpenRAM Verilog model" and "fault
  run against the shim-wrapped `fault_ram`" using the *same* testbench and
  port connections.

In both cases OpenRAM is optional in principle — the classic path works with
any memory module that matches your declared port names, and the algo-shell's
`fault_ram.sv` needs no real macro at all — but OpenRAM is the tool this
project is built to interoperate with directly, and the shim modules exist
purely to make that interoperation frictionless on the algo-shell side.

## See also

- `src/autombist/engine/README.md` — the algo-shell engine's own reference:
  exact fault-list/`.alg` grammar, the full 19-primitive semantics table,
  measured coverage numbers, multi-port (`march_engine_mp.sv`) syntax, and
  notes on using Cadence Xcelium instead of Verilator.
- The repository README — installation, prerequisites, and the full command
  walkthrough for both subsystems.
