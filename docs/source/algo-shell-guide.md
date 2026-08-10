# Algo-Shell Guide

```{admonition} Experimental
:class: warning
The algo-shell / research-mode subsystem (`autombist test`, `autombist algo`)
is experimental and under active development. Interfaces, report formats, and
the `.alg` grammar may still change. The classic path (`autombist generate` /
`simulate` / `run`) is the stable, production path.
```

This is the user guide for autoMBIST's **algo-shell** subsystem: the
research-oriented half of the tool that grades march algorithms (and
controller FSMs) against a 19-primitive functional fault model, using a
Verilator-driven behavioral RAM instead of a synthesizable memory macro. If
you haven't already, read {doc}`architecture` first — its "Two
subsystems, one repository" table and "The algo-shell" section explain how
this half relates to the classic RTL-wrapping path (`autombist generate` /
`simulate` / `run`) and why they're kept separate.

## 1. When to use this vs. the classic path

Reach for the algo-shell (`test` / `algo`) when the question is about the
**algorithm or controller**, not about a specific synthesizable memory: "does
March C- catch WDF faults?", "how does my hand-written march compare to
March SS?", "does this FSM actually detect what it claims to?" It needs no
real memory macro — `fault_ram.sv` is a behavioral stand-in — and it models
19 functional fault primitives (coupling, disturbs, decoder faults) that the
classic path's structural stuck-at/transition masks don't cover. Reach for
the classic path (`autombist generate` / `simulate` / `run`) when the
question is about a specific memory instance you intend to actually tape
out: generating synthesizable MBIST RTL around an OpenRAM-generated (or
otherwise real) macro, with cocotb + Icarus driving the simulation. The two
subsystems share a CLI and a Python package but touch disjoint code paths,
simulators, and report schemas — see {doc}`architecture` for the full
comparison table and rationale.

## 2. `autombist test` — batch/scripted command reference

`test` is the one-shot, scriptable entry point: it compiles the fault engine
once, runs a golden pass, runs one simulation per fault in your fault list,
and prints (and optionally writes) a coverage report. Use it from CI, a
Makefile, or any non-interactive workflow.

### Usage

```
autombist test --addr-width INTEGER --data-width INTEGER --faults PATH [OPTIONS]
```

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--addr-width`, `-aw` (required) | — | Memory address width in bits |
| `--data-width`, `-dw` (required) | — | Memory data width in bits |
| `--algo TEXT` | `march_c` | Built-in algorithm name (`march_c`, `march_c_plus`, `march_y`, `march_b`, `mats_plus`, `march_ss`, `march_x`) or a path to a `.alg` file |
| `--fsm PATH` | none | Validate a controller FSM `.sv` instead of an algorithm (takes precedence over `--algo`); sibling `.sv`/`.v` files in its directory are gathered automatically. No elem/op attribution in this mode — a black-box controller has no step counter to report |
| `--faults PATH` (required) | — | Fault-list file: `TYPE VADDR VBIT AADDR ABIT P0 P1` per line (see the `add_fault`/`load_faults` entries in §3 for the full grammar, and the primitive table in §4) |
| `--fault-types PATH` | none | JSON file with a list of custom fault-primitive specs, added to the built-in 19 (see §4 and `fault_primitives.py`'s module docstring for the schema) |
| `--init INTEGER` | `1` | Memory init value (0 or 1) |
| `--sim TEXT` | `verilator` | Simulator backend — Verilator only; Icarus cannot run the SV fault engine (it uses `foreach`, queues, and `final` blocks) |
| `--verbose` | off | Print per-fault activation counts (`+FAULT_VERBOSE`); ORed with the top-level `autombist -v` flag, so `autombist -v test ...` has the same effect without touching this flag |
| `--report PATH` | none | Write a per-fault coverage report to this path |
| `--fmt TEXT` | `md` | Report format: `md`, `csv`, or `json` |
| `--min-coverage FLOAT` | none | Exit 1 if coverage falls below this percent (useful as a CI gate) |
| `--diagnosis PATH` | none | Write a per-cell `(addr, bit)` diagnosis/fail-bitmap report to this path |
| `--diagnosis-fmt TEXT` | `md` | Diagnosis report format: `md`, `csv`, or `json` |
| `--check-sequence` | off | With `--fsm`: also verify the controller drives the exact march sequence of `--algo` (address order, ops, write data, port), independent of fault detection. Exits 1 on a sequence mismatch |
| `--json` | off | Print the full campaign result (same shape as `--report json`) as JSON to stdout instead of the human summary |

### Examples

```bash
autombist test --addr-width 8 --data-width 8 --algo march_c --faults faults.txt
autombist test -aw 10 -dw 32 --algo march_ss --faults faults.txt --verbose
autombist test -aw 8 -dw 8 --algo my_algo.alg --faults faults.txt --report cov.md
autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt --min-coverage 90
autombist test -aw 10 -dw 32 --fsm rtl/march_c/march_c_top.sv --faults faults.txt
autombist test -aw 10 -dw 32 --fsm rtl/march_c/march_c_top.sv --faults faults.txt --check-sequence
autombist test -aw 8 -dw 8 --algo march_ss --faults faults.txt --fault-types mytypes.json
autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt --diagnosis diag.md
autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt --json
```

A run against an 8x8 memory with a small hand-picked fault list looks like:

```
$ autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt
autombist test: march_c (10n) on 8x8 memory, init=1
  faults: 19   detected: 14   coverage: 73.68%
  build: 5.40s   run: 0.11s   sim: verilator
```

`--report` writes a per-fault table (index, type, victim/aggressor site,
P0/P1, DETECTED/ESCAPED, and — for algorithm runs, not `--fsm` runs — the
detecting element/op/address); `--diagnosis` instead writes a per-cell
"fail-bitmap" view, sparse over just the `(addr, bit)` sites that were
injected into or observed at, useful for spotting whether escapes cluster on
particular cells. `--min-coverage` makes `test` a gate: exit code 1 (with the
coverage percent on stderr) if the campaign falls short, so it composes with
CI or a Makefile target the same way the classic path's `--test` flag does.

`--check-sequence` (only meaningful with `--fsm`) is a second, independent
gate: it checks that the controller drives the *exact* march sequence
implied by `--algo` — address order, ops, write data, and port — regardless
of whether any fault happened to be detected. A mismatch prints a diagnostic
`sequence: MISMATCH` block (always, on stderr, regardless of `-q`/`--json`)
and exits 1 even if fault coverage looks fine; this catches controllers that
pass by accident (e.g. two elements swapped, or a wrong write value) rather
than by actually implementing the specified algorithm. `--json` prints the
full campaign result (same shape as `--report json`) to stdout instead of
the human-readable summary, so a script can `autombist test ... --json |
jq` without parsing status text. On a TTY (and unless `NO_COLOR` is set),
`test` also shows a live progress bar over the per-fault simulation loop.
The top-level `autombist -q` flag suppresses `test`'s routine
`autombist test: ...`/coverage summary lines the same way `--json` does
(results/errors still print).

The `build:`/`run:` split above reflects two independent speedups: `build:`
is usually a cache hit (a content-addressed build cache keyed on the
resolved source + toolchain version, so a repeated memory/algorithm
combination pays for the Verilator build once), and `run:` benefits from the
per-fault simulation loop's bounded concurrency (`AUTOMBIST_FAULT_CONCURRENCY`,
default 4 workers). Both apply identically to `autombist algo`'s `run`/
`compare_algo`. See `src/autombist/engine/README.md` for the full detail on
either.

## 3. `autombist algo` — the interactive research shell

`algo` launches a `cmd.Cmd`-based REPL for iterative work: register one or
more algorithms and/or FSMs, build up a fault list by hand or generated,
run campaigns, compare algorithms side by side, and export reports or a
standalone testbench bundle. Built-in algorithms (`march_c`, `march_c_plus`, `march_y`, `march_b`,
`mats_plus`, `march_ss`, `march_x`) are preloaded at start, so you can `run march_c`
immediately without an `add_algo` call.

```bash
autombist algo                             # interactive prompt
autombist algo --script session.algo       # replay commands from a file
printf 'set_memory 8 8\nrun march_c\nquit\n' | autombist algo --script -
```

`--script -` reads commands from stdin instead of a file, which is handy for
one-off scripted sessions or embedding in another script. Inside the shell,
`help` lists every command, and `help <command>` prints that command's exact
usage string.

### Worked session

This is a real transcript (memory addresses/bits below are illustrative,
not tied to a specific fault list):

```
algo> set_memory 8 8
memory set: 8x8, init=1, ports=1

algo> list
algos:
  march_b  (17n, 5 elements)
  march_c  (10n, 6 elements)
  march_c_plus  (14n, 6 elements)
  march_ss  (22n, 6 elements)
  march_x  (6n, 4 elements)
  march_y  (8n, 4 elements)
  mats_plus  (5n, 3 elements)
fsms:
  (none registered; use add_fsm)
faults: 0 loaded
fault types (usable in add_fault/load_faults/gen_faults): SA0, SA1, TF0, TF1, WDF0, WDF1, RDF0, RDF1, DRDF0, DRDF1, IRF0, IRF1, SOF, AF_NOACC, AF_ALIAS, CFIN, CFID, CFST, CFDS

algo> add_fault SA0 5 1
fault added: SA0 v=5.1 (total 1)

algo> add_fault CFIN 5 1 6 1 2 0
fault added: CFIN v=5.1 (total 2)

algo> gen_faults
generated 19 faults

algo> run march_c
march_c: 14/19 detected (73.68%)  build=5.40s run=0.11s

algo> write_report /tmp/algo_report.md
report written: /tmp/algo_report.md

algo> write_diagnosis /tmp/algo_diag.md
diagnosis written: /tmp/algo_diag.md

algo> compare_algo march_c -march SS,MATS
march_c: 14/19 detected (73.68%)  build=5.22s run=0.11s
march_ss: 18/19 detected (94.74%)  build=5.04s run=0.15s
mats_plus: 13/19 detected (68.42%)  build=6.71s run=0.10s

| fault          | march_c | march_ss | mats_plus |
| -------------- | ------- | -------- | --------- |
| SA0@3.0        | D       | D        | D         |
| ...
| total          | 14/19   | 18/19    | 13/19     |

algo> quit
```

Note `gen_faults` (with no session fault list to preserve) replaced the two
`add_fault` calls above with one instance of every built-in type — that's
its default behavior, described below. `compare_algo`'s `-march SS,MATS`
resolved through the shell's literature-style aliases (`SS` → `march_ss`,
`MATS` → `mats_plus`; see `ALGO_ALIASES` in `algo_shell.py` for the full
table: `C`/`C-`/`MARCHC` → `march_c`, `X`/`MARCHX` → `march_x`).

An `.alg`-file-driven session (rather than the preloaded built-ins) looks
the same from `add_algo` onward:

```
algo> set_memory 10 32
algo> add_algo my_march.alg --name mine
algorithm 'mine' registered (12n, 7 elements)
algo> load_faults faults.txt
loaded 40 faults from faults.txt (total 40)
algo> run mine --verbose
mine: 33/40 detected (82.50%)  build=5.1s run=0.4s
```

### Command reference

Every command below is documented from its actual `do_*` docstring (i.e.
`help <command>` inside the shell prints the same text).

**`set_memory <addr_width> <data_width> [--wmasks N] [--init 0|1] [--ports 1|2]`**
Configure the memory under test. `--ports` selects how many physical ports
the fault engine models (default 1); `2` enables genuine cross-port coupling
faults (see `add_fault`'s `VPORT`/`APORT` args) via `march_engine_mp.sv`.
Must be called before most other commands (`run`, `gen_faults`, etc. raise
an error otherwise).

**`add_algo <path.alg> [--name NAME]`**
Register a march algorithm spec from a `.alg` file (format in §5). `--name`
defaults to the file's stem.

**`add_fsm <top.sv> [<dep1.sv> <dep2.sv> ...] [--name NAME] [--top MODULE]`**
Register a controller FSM (must expose the March-FSM port contract:
`clk`/`rst_n`/`bist_start` in, `bist_done`/`bist_fail` out, `sram_*` bus).
With a single file, sibling `.sv`/`.v` files in the same directory are
pulled in automatically (matches this repo's `rtl/<algo>/` layout); pass
multiple files explicitly to override that. If the session's memory was
configured with `set_memory --ports 2`, the FSM is validated against the
2-port contract (`sram_*0` **and** `sram_*1` buses both required);
otherwise the single-port contract applies.

**`add_fault_type <json>` or `add_fault_type --file <path.json>`**
Define a new fault primitive from a JSON spec (full DSL reference in §4).
Takes effect on the next `run`/`compare_algo` — `fault_ram.sv` is
regenerated from the updated registry each time.

**`add_fault TYPE VADDR VBIT [AADDR ABIT P0 P1 [VPORT APORT]]`**
Append one fault instance to the current fault list. `AADDR ABIT P0 P1`
default to `0 0 0 0` when omitted (valid for single-parameter faults like
`SA0`/`SA1`). `APORT` (default 0) selects which physical port the aggressor
access is on — meaningful only for the coupling-class primitives
(`CFIN`/`CFID`/`CFST`/`CFDS`) in a 2-port memory. `VPORT` is parsed but not
yet honoured by the generated engine — the victim-side guards match on
address and bit alone, so setting it currently changes nothing for any fault
type; it is reserved for a future per-port victim gate. See
{doc}`multi-port-guide` §3b for the full same-port-vs-cross-port semantics.

**`load_faults <path> [--append]`**
Load a fault-list file (`add_fault`'s grammar, above), replacing the current
list unless `--append`.

**`gen_faults [--all-types] [--n N --seed S]`**
Generate a fault list: one instance of each of the 19 built-in types
(default), or `N` random faults with `--n`/`--seed` for reproducibility.
This *replaces* the session's current fault list.

**`run <algo_name|fsm_name> [--verbose] [--check ALGO] [--backgrounds]`**
Run a fault campaign for one registered algorithm or FSM against the
current fault list. FSM runs report detect/escape only — `--verbose` has no
effect for them (no elem/op step counter on a black-box controller). Stores
the result as "last op" for `write_report`/`write_diagnosis`/
`write_syndrome`. `--check ALGO` (FSM targets only; `ALGO` is a built-in
name or a `.alg` path) additionally verifies the controller drives the
exact march sequence of `ALGO` — address order, ops, write data, port —
independent of fault detection; the result prints a `sequence: OK`/
`MISMATCH` line alongside the usual detect/escape summary. `--backgrounds`
(algorithm targets only) also runs the standard intra-word data-background
set (solid + column-stripe patterns) and merges results — a fault counts as
detected if *any* background caught it; the merged result's
`backgrounds_run` field lists which ran. FSM targets reject this flag
(`openram_shim.sv` has no `+BACKGROUND` path).

**`compare_algo <name> -march NAME1,NAME2,... [--backgrounds]`**
Run `<name>` plus each comma-separated named algorithm (aliases like `C`,
`SS`, `MATS`, `X` resolve automatically) against the current fault list and
print a fault-by-fault detect/escape matrix. Stores the result set as "last
op" (a matrix, not a single run). `--backgrounds` also runs the standard
intra-word data-background set per algorithm (see `run --backgrounds`).

**`write_report <path> [--fmt md|csv|json]`**
Persist the most recent `run` or `compare_algo` result (whichever ran last)
to a file in the given format.

**`write_diagnosis <path> [--fmt md|csv|json]`**
Persist a per-cell `(addr, bit)` diagnosis/fail-bitmap report for the most
recent `run` result only. If the last op was `compare_algo`, this raises an
error instead — a cross-algorithm diagnosis has no single obvious cell-table
shape, so diagnose each algorithm individually via `run`.

**`write_syndrome <path> [--fmt md|csv|json]`**
Persist a blind syndrome-ambiguity report for the most recent `run` result:
groups the injected fault *types* by identical (detect/escape, elem, op)
signature, flagging groups with 2+ types as `ambiguous` — this algorithm
alone cannot distinguish them. Same `compare_algo`-rejection rule as
`write_diagnosis`. See [diagnosis-reports.md](diagnosis-reports.md#6-blind-syndrome-based-diagnosis-fault-type-ambiguity)
for the full schema and a worked example.

**`export_tb <dir>`**
Dump a self-contained, standalone-runnable testbench bundle into `<dir>`:
the engine sources (`fault_ram.sv` regenerated from the session's registry,
so any custom types from `add_fault_type` are included), every registered
`.alg` spec (as numeric `.algc` files), and the current fault list —
runnable via the bundle's `run_campaign.sh` without autoMBIST installed.

**`list [algos|fsms|faults|types]`**
Inspect session state; defaults to printing everything. `list types` shows
the 19 built-in fault-type names usable in `add_fault`/`load_faults`/
`gen_faults`, plus any custom types registered via `add_fault_type` in a
separate "custom types" line.

**`status`**
One-screen summary: memory config (including port count if >1), simulator,
registered algorithm/FSM counts and names, fault-list size, and the
session's scratch workdir.

**`set_sim verilator`**
Select the simulator backend. Verilator is the only supported value — the
fault engine (`fault_ram.sv`) uses SystemVerilog queues, `foreach`, and
`final` blocks, none of which Icarus Verilog supports.

**`quit`** / **`EOF`** (Ctrl-D)
Exit the shell.

## 4. The fault-primitive DSL (`add_fault_type`)

autoMBIST's fault engine (`fault_ram.sv`) natively implements 19 functional
fault primitives. Fifteen of them are generated from a small declarative
DSL (`fault_primitives.py`); the remaining four are fixed, hand-written
scaffolding that doesn't fit the DSL's shape. `add_fault_type` lets you
define **new** fault types in the DSL's terms — no SystemVerilog editing —
and they compose with the 19 built-ins in the same fault list and reports.

### What the DSL can express

A fault primitive is a JSON object with this shape:

```json
{
  "name": "MYCF",
  "category": "write_effect",
  "sensitize": {"transition": "p0", "on": "aggressor"},
  "effect": {"kind": "invert"}
}
```

- **`name`** — an uppercase identifier (`^[A-Z][A-Z0-9_]*$`), distinct from
  every built-in and every already-registered custom type.
- **`category`** — one of three fault families the DSL models:
  - `static_clamp` — the victim bit is always forced to a value, independent
    of any operation (like `SA0`/`SA1`/`CFST`).
  - `write_effect` — the fault is sensitized by a write, either to the
    victim cell itself or to a coupled aggressor cell.
  - `read_effect` — the fault is sensitized by a read of the victim cell.
  - (Address-decoder faults — a whole address aliasing or dropping out —
    are *not* expressible via `add_fault_type`; they run in a structurally
    different pre-pass over the effective address, before any per-bit case
    arm. `AF_NOACC`/`AF_ALIAS` remain the only address-decoder faults.)
- **`sensitize`** — the condition that must hold for the effect to fire:
  - `pre` — required pre-op value of the gating bit (`"0"`, `"1"`, `"p0"`,
    `"p1"`, or `"x"` for don't-care).
  - `written` — required written value, for `write_effect`/victim-gated
    faults only (same token set).
  - `transition` — for `write_effect` faults gated by an *aggressor's*
    write, which transition triggers it: `"up"`, `"down"`, `"either"`,
    `"p0"` (parameterized — P0 selects at runtime), or `"x"`.
  - `on` — whose access gates the fault: `"victim"` or `"aggressor"`. Only
    `"aggressor"`-gated faults are genuine coupling faults (two cells
    involved); `"victim"`-gated faults are single-cell.
  - `port` — which physical port the sensitizing op must occur on: `"0"`,
    `"1"`, or `"x"` (wildcard — matches on address alone, the implicit
    behavior of every existing built-in). Only meaningful in a 2-port
    (`set_memory --ports 2`) session; a non-`"x"` port is **rejected** when
    the session is single-port, because the generated engine has no `port`
    argument to gate on there.

    Two further restrictions, both refused at `add_fault_type` time rather
    than silently ignored:
    - **not for `static_clamp`.** A clamp rewrites *stored* state and is
      re-asserted after every access, so every port would see the corruption
      anyway — port-scoping it is not expressible. Model a port-specific
      read-path defect as `read_effect`, whose effect lands on the returned
      value and so is naturally per-port. (Same structural reason the built-in
      CFST cannot honour a fault line's `APORT`.)
    - **not with `raw_sv`.** The gate is emitted by wrapping the generated
      arm's condition; a `raw_sv` body is copied verbatim, so the constraint
      would be dropped. Gate on the arm's own `port` argument inside your
      `raw_sv` text and leave `port` as `"x"`.
- **`effect`** — what happens to the victim when sensitized:
  - `kind` — `force` (clamp to a value), `invert` (flip the bit),
    `block_write` (the write silently fails to update the cell),
    `corrupt_read` (the *returned* read value is wrong, but the cell's
    stored value is untouched), or `force_read` (the cell's stored value
    changes to `value`, and the read returns `also_read` if given, else
    `value`).
  - `value` — `"0"`, `"1"`, `"p0"`, or `"p1"` (a literal or a
    runtime-supplied fault parameter); required unless `kind` is `invert`.
  - `also_read` — only meaningful with `force_read`: models a
    disturb-with-wrong-readback (DRDF-style) fault where the stored value
    changes but the read masks it.
  - `target` — `"victim"` or `"aggressor"`; every built-in targets
    `"victim"`.
- **`params_help`** (optional) — a `{param_name: description}` map purely
  for documentation of what P0/P1 mean for this type; not used by codegen.
- **`raw_sv`** (optional) — an escape hatch: hand-write the case-arm body
  verbatim (for any of the three DSL-coverable categories) instead of
  using the `sensitize`/`effect` fields, which are then ignored for code
  generation. See `fault_primitives.py`'s module docstring and
  `fault_ram_gen.py` for the exact per-site variable scope available to
  `raw_sv`.

Each category constrains which `effect.kind` values are legal (for example,
`static_clamp` primitives must use `force`; `read_effect` primitives must
use `corrupt_read` or `force_read` and must be victim-gated) — `add_fault_type`
validates all of this and raises a descriptive error before accepting the
type. A registered custom type is used exactly like a built-in afterward, in
`add_fault`, fault-list files, and `gen_faults`.

From the shell:

```
algo> add_fault_type {"name": "MYCLAMP", "category": "static_clamp", "effect": {"kind": "force", "value": "1"}}
fault type 'MYCLAMP' registered (static_clamp); takes effect on the next run

algo> list types
fault types (usable in add_fault/load_faults/gen_faults): SA0, SA1, TF0, TF1, WDF0, WDF1, RDF0, RDF1, DRDF0, DRDF1, IRF0, IRF1, SOF, AF_NOACC, AF_ALIAS, CFIN, CFID, CFST, CFDS
custom types (added via add_fault_type): MYCLAMP
```

Or from a file, via `add_fault_type --file mytype.json` (shell) or
`autombist test --fault-types mytypes.json` (batch — note the batch flag
takes a JSON **list** of specs, not a single object).

### The 4 fixed built-in types

These are never expressible via `add_fault_type` — they're baked into every
generated `fault_ram.sv` as fixed scaffolding, because each breaks one of the
DSL's structural assumptions:

| Type | What it means | Why it's fixed, not DSL-expressible |
|---|---|---|
| `SOF` (Stuck-Open Fault) | The victim cell becomes inaccessible: writes are silently dropped, and reads return whatever the *previous* read happened to return (an "output keeper" effect) | Its read-path behavior reads cross-operation state (the module-level `dout` register) directly, which is outside the per-op locals the DSL's `read_effect` category models |
| `AF_NOACC` (Address-decoder, no access) | The target address doesn't decode to any real cell: writes are dropped, reads return a constant `P0` on every bit | Runs in an address-decoder *pre-pass*, before the per-bit loop, mutating the effective address for every subsequent bit — a structurally different insertion site than a per-bit case arm |
| `AF_ALIAS` (Address-decoder, alias) | Accesses to `VADDR` land on a different word, `AADDR`, instead | Same pre-pass structural issue as `AF_NOACC` |
| `CFDS` (Coupling Fault, Disturb by State/read) | An operation on the aggressor cell disturbs (inverts) the victim; `P0` selects *which* aggressor operation triggers it: `0`=read-0, `1`=read-1, `2`=non-transition write-0, `3`=non-transition write-1, `4`=any read | Its single `P0` parameter actually selects among five distinct sensitizing conditions spanning *both* the write-aggressor and read-aggressor code paths — it's really a union of several fault types under one name, not a single-site effect |

For deep RTL-level detail on all 19 primitives — the full semantics table
with `<sensitizing-op/faulty-value/faulty-read>` notation, measured
detect/escape results for the built-in march algorithms, and notes on how
static clamps interact with coupling effects on the same bit — see
`src/autombist/engine/README.md`'s "Fault primitive semantics" and "Measured
results" sections; this guide summarizes the parts relevant to
`add_fault_type` rather than duplicating that table.

## 5. The `.alg` march-algorithm file format

A `.alg` file describes a march algorithm as one **element** per line:

```
DIR OP [OP ...]
```

- `DIR` — the address order for this element: `up`, `down`, or `either`
  (order doesn't matter for this element's correctness).
- `OP` — one or more operations applied at each address, in sequence, from
  `{r0, r1, w0, w1}` (read-expect-0, read-expect-1, write-0, write-1).

Blank lines and `#` comments are ignored. March C- (one of the four
built-ins) looks like:

```
either w0
up   r0 w1
up   r1 w0
down r0 w1
down r1 w0
either r0
```

That's 6 elements totaling 10 operations per address ("10n" in coverage
output). A march spec is limited to 16 elements with up to 8 operations
each (the SystemVerilog engine's fixed-size `prog[16]`/`ops[8]` arrays).

Load one with `add_algo my_march.alg` in the shell, or pass its path
directly to `--algo` on `autombist test`.

### How `either` gets resolved

`either` is a statement about the *algorithm* — this element detects what it
detects regardless of address order — so a consumer that needs one concrete
address order has to pick. Every consumer in this project picks the same way,
via one function: **an `either` element inherits the direction of the
previous element, defaulting to up when there is none**
(`alg_spec.resolve_directions()`). The numeric form the SystemVerilog engines
read (`AlgSpec.to_numeric()`), the classic-path RTL table renderer, the FSM
reference trace, and the synthesizer's internal replay model all call this
one function rather than resolving independently.

So March C-'s trailing `either r0` resolves to **down** — it follows two
`down` elements — both when `march_engine.sv` runs the algorithm from a
campaign and in the hand-written classic RTL that ships to silicon.

This rule is not arbitrary. It is the one real silicon already implements —
proven by an exhaustive simulation sweep comparing the rendered classic-path
table against the hand-written one it replaces, for every algorithm that has
both a `.alg` spec and hand-written RTL (`march_c`, `march_x`, `mats_plus`)
(`tests/integration/test_algo_table_equivalence.py`, 32/32 vectors per
algorithm) — and it has a hardware rationale: continuing in the same direction
means the address counter never has to rewind between elements.

**Getting this right is not cosmetic.** Before the rule was unified,
`march_engine.sv` ran every `either` element ascending regardless of context.
On `faults.example.txt` at `addr_width=8`/`data_width=8`, that made
`run_algo_campaign` under-report March X's coverage as 12/19 instead of the
13/19 the algorithm actually detects: `CFDS 130 1 131 1 4 0` (aggressor above
the victim) is caught only by a descending final read, and March X's trailing
`either r0` is exactly that read. March C-'s own trailing `either` happens to
be direction-insensitive for every fault in that list — which is why the two
subsystems could disagree there for years without a coverage number ever
looking wrong.

The one place `either`'s freedom is still honored rather than collapsed to a
single answer is the FSM sequence checker (`--check-sequence`): it validates
an arbitrary hand-written controller against a spec, and a controller is free
to choose either address order for a genuine `either` element, so it accepts
both — with the direction `resolve_directions()` picks reported first in any
divergence message.

### Port suffixes (multi-port)

Any op token may carry an optional `.PORT` suffix — `r0.1`, `w1.0`, etc. —
meaning "issue this op on physical port `PORT`" (`PORT` in `{0, 1}`).
Omitting the suffix (every built-in `.alg` file) means port 0, so a plain
single-port `.alg` file's meaning is unchanged whether or not the memory is
configured with `set_memory --ports 2`. A 2-port march element might read:

```
either w0            # init, port 0 (implicit)
up   w1.1            # up-transition write to every word, on port 1
up   r1              # read every word, on port 0 (implicit)
```

Port suffixes only matter once the session (or `MemoryParams`) is configured
for `num_ports=2`, which switches the engine to `march_engine_mp.sv` and
enables the corresponding 9-field fault-list format (trailing `VPORT
APORT` columns) for genuine cross-port coupling faults. See
`src/autombist/engine/README.md`'s "Multi-port" section for the full
grammar and same-port-vs-cross-port semantics, and {doc}`architecture`'s
"multi-port invariant" section for why this is one shared engine rather
than a fork.
