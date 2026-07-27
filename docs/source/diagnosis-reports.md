---
orphan: true
---

# Diagnosis / Fail-Bitmap Reports

This page documents autoMBIST's **per-cell diagnosis** reporting: the feature that
tells you *which specific (address, bit) cells* a fault campaign caught or missed,
rather than just an aggregate `detected/total` count.

It covers both places this shows up:

- the **algo-shell / functional-fault engine** (`autombist test`, `autombist algo`) —
  an explicit, opt-in `--diagnosis` report with a sparse address-by-bit cell table;
- the **classic path** (`autombist run --test`, `autombist generate --test` +
  `autombist simulate`) — the `fault_details` field in the JSON results report,
  which is unconditional, not an opt-in flag.

For the rest of each path's flags and outputs, see
[cli-reference.md](cli-reference.md) (classic path) and
[algo-shell-guide.md](algo-shell-guide.md) (functional-fault engine / interactive
shell).

---

## 1. Why this exists

A fault campaign's headline number is a coverage percentage: `14/19 (73.68%)`,
`detected/total`. That number answers "how good is this algorithm overall," but it
cannot answer the question a hardware bring-up engineer or test engineer actually
asks when triaging a failing part or tuning an algorithm:

- *Which* memory cells were exercised at all?
- Of the faults injected, which specific `(address, bit)` locations were caught,
  and which escaped?
- When a fault *was* caught, where did the March algorithm actually observe the
  mismatch — was it at the same cell the fault was injected into, or somewhere
  else entirely?

Aggregate counts collapse all of that into one ratio. Two campaigns can both
report "73.68% coverage" while missing completely different cells — one leaving a
single stuck-at bit undetected in a corner of the array, the other missing an
entire coupling class. You cannot tell them apart, or decide whether the escapes
are acceptable, from the percentage alone.

Diagnosis reporting exists to answer those questions directly: it surfaces a
table (or JSON array) with one row per memory cell that was touched by the fault
campaign, in either direction — as an injection site, an observation site, or
both — so you can see exactly where the algorithm succeeded and exactly where it
didn't.

---

## 2. The algo-shell diagnosis report (`--diagnosis`)

This report is produced by `src/autombist/algo_reporting.py`
(`build_diagnosis_cells()` / `write_diagnosis_report()`), and is wired into both
the one-shot CLI and the interactive shell. It applies to a **single** campaign
result (one algorithm run against one fault list) — it does not apply to
multi-algorithm comparison runs (see [Limitations](#5-scope-and-limitations)
below).

### 2.1 Requesting it

**From the one-shot `autombist test` command**, pass `--diagnosis PATH` alongside
the existing `--faults`/`--algo` options, with an optional `--diagnosis-fmt`:

```bash
autombist test -aw 8 -dw 8 --algo march_c --faults faults.txt \
  --diagnosis diag.md --diagnosis-fmt md
```

`--diagnosis-fmt` accepts `md` (default), `csv`, or `json`. This is a separate
flag from the pre-existing `--report`/`--fmt` pair (the per-fault coverage
report) — you can request both a `--report` and a `--diagnosis` from the same
run, in different formats, and they never interfere with each other.

`test` also has a top-level `--json` flag, but don't confuse it with
`--diagnosis-fmt json`: `--json` prints the campaign's raw per-fault list
(`{schema_version, algo_name, mem, faults: [...], ...}`, one entry per
`FaultResult` with its `vaddr`/`vbit`/`addr`/`xor`/`detected` fields — the same
data `--report json` writes to a file) straight to stdout, not the aggregated
per-`(addr, bit)` cell table described below. Reach for `--diagnosis` when you
want the cell-table view; reach for `--json` when you want the flat per-fault
record list without writing a file.

**From the interactive shell** (`autombist algo`), after a `run <algo_name>`,
use the `write_diagnosis` command:

```
(autombist) run march_c
(autombist) write_diagnosis diag.md --fmt md
diagnosis written: diag.md
```

`write_diagnosis` only works after a `run` (single-algorithm) op — if the last
operation was `compare_algo` (a multi-algorithm matrix), it raises an error
instead, telling you to `run <algo_name>` for the specific algorithm you want to
diagnose. A cross-algorithm diagnosis view has no single obvious cell-table
shape, so this is a hard restriction, not a missing feature.

### 2.2 Cell-table schema

Both formats (CSV and Markdown are literally the same rows, just rendered
differently; JSON keeps list-valued fields as real JSON arrays) share these
columns/fields per cell:

| Field | Type | Meaning |
|---|---|---|
| `addr` | int | Memory address of this cell. |
| `bit` | int | Bit index within the word at that address. |
| `role` | `"injection"` \| `"observation"` \| `"both"` | Whether this `(addr, bit)` appeared as a fault **injection site** (some fault's victim address/bit), an **observation site** (a bit that mismatched when the algorithm read back some address), or both. |
| `fault_types_injected_here` | list of str | Fault-type names (e.g. `SA0`, `CFIN`) injected with this cell as their victim `(vaddr, vbit)`. Empty if this cell is observation-only. |
| `detected_as_injection` | bool | True if **any** fault injected at this cell was detected (caught) by the algorithm. If two faults share a cell (e.g. `SA0` and `SA1` at the same `addr.bit`) and only one is caught, this is still `True` — see `escaped_types_here` for the fault-level detail this flag can't express alone. |
| `escaped_types_here` | list of str | Fault-type names injected at this cell that were **not** detected, tracked per fault (not derived from `detected_as_injection`) — so if `SA0` here is caught but `SA1` here escapes, `SA1` still shows up in this list even though `detected_as_injection` is `True` for the cell overall. |
| `times_observed_mismatch` | int | How many times a detected fault's readback mismatch was decoded as landing on this exact `(addr, bit)`. Usually 0 or 1 per fault, but can accumulate across multiple faults that all get observed at the same cell. |
| `observed_from_fault_types` | list of str | Fault-type names whose detection was observed (mismatch decoded) at this cell. Empty if this cell was never an observation site. |

The table is **sparse**: it only includes rows for cells that actually appear as
an injection site, an observation site, or both — never the full `addr × bit`
grid, which would be almost entirely empty rows for any realistically sized
memory.

Escaped injection sites still get a row (with empty observation-side fields):
a fault that was injected but never observed is exactly the information a
fail-bitmap diagnosis exists to surface, and it must not silently vanish from
the table just because nothing was ever observed there.

List-valued fields (`fault_types_injected_here`, `escaped_types_here`,
`observed_from_fault_types`) are pipe-joined (`|`) in the CSV/Markdown renderers,
since neither format has a native list type; the JSON renderer keeps them as
real JSON arrays.

### 2.3 Worked example

Run against this repo's own `src/autombist/engine/faults.example.txt` (19 faults,
one of each of the 19 built-in fault types) on an 8×8 memory with `march_c`:

```bash
autombist test -aw 8 -dw 8 --algo march_c --faults faults.example.txt \
  --diagnosis diag.md --diagnosis-fmt md
```

```
autombist test: march_c (10n) on 8x8 memory, init=1
  faults: 19   detected: 14   coverage: 73.68%
  build: 1.89s   run: 0.13s   sim: verilator
  diagnosis: diag.md
```

The resulting `diag.md` (34 sparse cell rows out of a possible 256×8 grid):

```markdown
# autombist diagnosis — march_c

Memory: 8x8, init=1
Coverage: **14/19 (73.68%)**
Cells: 34 (sparse: injection and/or observation sites only)

| addr | bit | role        | fault_types_injected_here | detected_as_injection | escaped_types_here | times_observed_mismatch | observed_from_fault_types |
| ---- | --- | ----------- | -------------------------- | ---------------------- | ------------------- | ------------------------- | --------------------------- |
| 10   | 3   | both        | SA0                        | True                    |                      | 1                          | SA0                          |
| 17   | 0   | both        | SA1                        | True                    |                      | 1                          | SA1                          |
| 40   | 1   | injection   | WDF0                       | False                   | WDF0                 | 0                          |                              |
| 80   | 0   | both        | AF_NOACC                   | True                    |                      | 1                          | AF_NOACC                     |
| 80   | 1   | observation |                             | False                   |                      | 1                          | AF_NOACC                     |
| 90   | 0   | injection   | AF_ALIAS                   | True                    |                      | 0                          |                              |
| 91   | 0   | observation |                             | False                   |                      | 1                          | AF_ALIAS                     |
| 100  | 2   | both        | CFIN                       | True                    |                      | 1                          | CFIN                         |
```

(Trimmed for readability — the full table has all 34 rows, including all 8 bits
of `addr=80` and `addr=91` from the `AF_NOACC`/`AF_ALIAS` address-decoder faults;
see [Section 4](#4-address-decoder-vs-coupling-faults-a-non-obvious-distinction)
below for why those two rows span whole words while the coupling rows don't.)

The equivalent CSV (`--diagnosis-fmt csv`) is the same rows, comma-separated,
with the header as the first line:

```text
addr,bit,role,fault_types_injected_here,detected_as_injection,escaped_types_here,times_observed_mismatch,observed_from_fault_types
10,3,both,SA0,True,,1,SA0
17,0,both,SA1,True,,1,SA1
40,1,injection,WDF0,False,WDF0,0,
80,0,both,AF_NOACC,True,,1,AF_NOACC
80,1,observation,,False,,1,AF_NOACC
90,0,injection,AF_ALIAS,True,,0,
91,0,observation,,False,,1,AF_ALIAS
100,2,both,CFIN,True,,1,CFIN
```

And the equivalent JSON (`--diagnosis-fmt json`) keeps list fields as real
arrays, and wraps the cells in a top-level object with campaign metadata:

```json
{
  "schema_version": "1.0.0",
  "algo_name": "march_c",
  "mem": { "addr_width": 8, "data_width": 8, "init_val": 1, "num_ports": 1 },
  "detected": 14,
  "total": 19,
  "coverage_percent": 73.68421052631578,
  "cells": [
    {
      "addr": 10, "bit": 3, "role": "both",
      "fault_types_injected_here": ["SA0"],
      "detected_as_injection": true,
      "escaped_types_here": [],
      "times_observed_mismatch": 1,
      "observed_from_fault_types": ["SA0"]
    },
    {
      "addr": 90, "bit": 0, "role": "injection",
      "fault_types_injected_here": ["AF_ALIAS"],
      "detected_as_injection": true,
      "escaped_types_here": [],
      "times_observed_mismatch": 0,
      "observed_from_fault_types": []
    },
    {
      "addr": 91, "bit": 0, "role": "observation",
      "fault_types_injected_here": [],
      "detected_as_injection": false,
      "escaped_types_here": [],
      "times_observed_mismatch": 1,
      "observed_from_fault_types": ["AF_ALIAS"]
    }
  ]
}
```

(All three examples above are real output from a real `verilator`-backed run of
this campaign against this repo's own example fault list, not hand-written.)

---

## 3. The classic path's `fault_details` field

The classic path (`autombist generate --test` + `autombist simulate`, or the
convenience wrapper `autombist run --test`) produces its results as a single JSON
report at `out/<memory_name>/reports/latest.json` (schema `"1.2.0"`, built by
`build_simulation_report()` in `src/autombist/reporting.py`). That report always
contains a `fault_details` array.

Both `autombist simulate` and `autombist run` accept a `--json` flag that
prints this same report — `fault_details` and, when a fail-scan ran,
`fail_bitmap` included — directly to stdout instead of (only) the files under
`reports/`, which is convenient for piping straight into `jq` without opening
`latest.json` separately.

### 3.1 How it differs from `--diagnosis`

This is the most important practical difference between the two paths:

- **`--diagnosis` (algo-shell) is opt-in** — you must pass `--diagnosis PATH` (or
  run `write_diagnosis` in the shell) to get it; it is a per-cell table computed
  after the fact from the campaign's structured `FaultResult`/`FaultRecord` data.
- **`fault_details` (classic path) is unconditional** — every `run --test` /
  `generate --test` + `simulate` invocation populates it, with no flag to turn it
  on or off. It is present (possibly as an empty list) in every `results.json`
  regardless of what else you asked for.

Mechanically, `fault_details` is not built from Python-side campaign state at
all: it is threaded straight through from the cocotb testbench's own stdout.
The testbench (`tests/hardware/test_mbist.py`) prints one `FAULT_SITE {json...}`
line per fault site it evaluated — one for every **detected** fault (built from
the ASCII fault-summary table row: `#`, `TYPE`, `ADDR`, `BIT`, plus fault-model-
specific value columns) and one for every **escaped** fault (the raw selected-
fault-site dict: `addr`, `bit`, `fault_value`, `kind`) — each tagged with
`"status": "detected"` or `"status": "escaped"`. `parse_fault_site_lines()` in
`reporting.py` scans stdout (and stderr, defensively) for lines matching
`^FAULT_SITE (.+)$` and JSON-decodes the remainder of each match; lines that fail
to parse as JSON are silently skipped rather than raising, since simulator stdout
can be interleaved or truncated (e.g. on a timeout). The resulting list of dicts
becomes `report["fault_details"]` verbatim — no schema normalization is applied
across detected/escaped rows, so the two kinds of entries in the list have
**different key sets** (see the worked example below).

### 3.2 Schema

`fault_details` is a JSON array; each element is one of two shapes, distinguished
by its `status` field:

**Detected fault-site entry** (built from the printed ASCII table row) — for
stuck-at / port-coupling fault models:

| Field | Meaning |
|---|---|
| `#` | Row number (string) in the printed fault-summary table. |
| `TYPE` | Fault kind, e.g. `SA0`, `SA1`, `PC`. |
| `ADDR` | Address in hex, e.g. `"0x0004"`. |
| `BIT` | Bit index (string). |
| `ACTUAL` | Golden/internal reference bit value observed by the harness. |
| `FAULT` | Bit value imposed by fault masking at that site. |
| `READ` | Bit value observed on MBIST readback. |
| `status` | `"detected"`. |

For transition fault models (`transition-up`/`transition-down`), the detected
row instead carries `PREV`/`ATTEMPT`/`READ`/`OBS` columns (see
[cli-reference.md](cli-reference.md) for the full transition-fault table
semantics) in place of `ACTUAL`/`FAULT`/`READ`.

**Escaped fault-site entry** (the raw selected-site record, undetected) —
same shape for every fault model:

| Field | Meaning |
|---|---|
| `addr` | Address, as an integer (not hex-string — contrast with the detected shape's `ADDR`). |
| `bit` | Bit index, integer. |
| `fault_value` | The stuck/fault value selected for this site (`-1` for transition/port-coupling sites, where there is no single fixed fault value). |
| `kind` | Fault kind, e.g. `SA1`, `TF-UP`, `PC`. |
| `status` | `"escaped"`. |

### 3.3 Worked example

This is drawn directly from this repo's own test fixtures
(`tests/software/test_reporting.py`), which assert on exactly this shape:

```
FAULT_SITE {"#": "1", "TYPE": "SA0", "ADDR": "0x0004", "BIT": "3", "ACTUAL": "0", "FAULT": "1", "READ": "1", "status": "detected"}
FAULT_SITE {"addr": 7, "bit": 2, "fault_value": 1, "kind": "SA1", "status": "escaped"}
```

parses into `report["fault_details"]`:

```json
[
  {
    "#": "1",
    "TYPE": "SA0",
    "ADDR": "0x0004",
    "BIT": "3",
    "ACTUAL": "0",
    "FAULT": "1",
    "READ": "1",
    "status": "detected"
  },
  {
    "addr": 7,
    "bit": 2,
    "fault_value": 1,
    "kind": "SA1",
    "status": "escaped"
  }
]
```

Note the two entries do not share a key set — this is expected, not a bug: the
"detected" shape mirrors the printed ASCII table row (hex address string, named
per-fault-model columns), while the "escaped" shape is the underlying fault-site
selection record (integer address, no observed values, since nothing was ever
observed there). If your tooling consumes `fault_details`, branch on `status`
before reading fields, rather than assuming one uniform schema across the array.

If no `FAULT_SITE` lines appear in the captured stdout/stderr at all (e.g. a run
that produced no fault summary), `fault_details` is simply `[]` — an empty list,
still present, never an absent key.

### `fail_bitmap` — the observation-derived fail scan (opt-in)

`fault_details` is **injection-gated**: it only ever reports the cells the
saboteur was told to fault (`selected_by_location`), and its detected/escaped
verdicts come from the saboteur's own debug taps. That is the right shape for
*coverage measurement*, but it cannot report a defect the saboteur did not
inject — so it is not, by itself, the fail map a repair flow needs for a memory
with an *unknown* defect.

`fail_bitmap` closes that gap. When a simulation is run in fail-scan mode
(`run_simulation(fail_scan=True)`, which selects `FAULT_MODE=failscan` and runs
`test_mbist.test_fail_scan`), the harness walks **every** cell through the
wrapper's functional port — writing a solid-0 then solid-1 pattern and reading
it back — and prints one `FAIL_CELL {json}` line per cell whose read-back
differs from what was written, followed by a `FAIL_SCAN_COMPLETE` marker:

```
FAIL_CELL {"addr": 3, "bit": 0}
FAIL_CELL {"addr": 15, "bit": 3}
FAIL_SCAN_COMPLETE cells=2
```

`reporting.parse_fail_bitmap_lines()` decodes these into
`report["fail_bitmap"]` — a deduplicated, `(addr, bit)`-sorted list of
`{"addr": int, "bit": int}`. Because `func_dout` comes straight off the memory's
read path (and the macro is always clocked), this observes the memory's **real**
output — so it reports a hard defect in the memory itself, not only injected
saboteur faults, and it is **ungated** (no injected-fault-list filter). That is
exactly the input a Built-In Redundancy Analysis (BIRA) step consumes.

Two properties matter for consumers:

- The key is **present only when a scan actually ran** (the marker was seen). A
  clean scan that found nothing reports `"fail_bitmap": []` (present, empty); a
  run that never scanned omits the key entirely. This keeps every ordinary
  (non-fail-scan) report byte-identical — which is why `schema_version` is not
  bumped for this addition.
- `bira_input.fail_cells(report)` is the uniform adapter: it unions `fail_bitmap`
  with the DETECTED entries of `fault_details` (escapes excluded) and returns a
  single integer-keyed `set[(addr, bit)]`, so a repair-analysis consumer never
  has to branch on report provenance or decode the hex-string `ADDR`/`BIT` form.

---

## 4. Address-decoder vs. coupling faults: a non-obvious distinction

**Coupling-class faults (`CFIN`, `CFID`, `CFST`, `CFDS`) never show
injection-vs-observation-site divergence in this engine.** Only address-decoder
faults (`AF_ALIAS`) genuinely redirect the address and can diverge. This is a
real, easy-to-get-wrong assumption, so it is worth stating explicitly rather than
letting users infer it (wrongly) from the word "coupling."

Why: a coupling fault, by definition in this engine, corrupts the **victim
cell's own storage location** — some access to the aggressor cell (`aaddr,
abit`) flips or forces the bit at the victim's own `(vaddr, vbit)`. The RTL
(`fault_ram.sv`) implements this literally as `mem[FQ[i].va][FQ[i].vb] = ...` for
all four coupling types (`T_CFIN`, `T_CFID`, `T_CFST`, `T_CFDS`) — the aggressor
access only *triggers* the corruption; it is always written into the *victim's*
cell. So whenever the algorithm later reads back address `vaddr` (the same
address the fault was injected at), that's where the mismatch is decoded. In the
diagnosis table, this is why every coupling-fault row above shows `role: both`
at the **same** `addr`/`bit` pair — the injection site and the observation site
are the same cell, always.

`AF_ALIAS`, by contrast, is an actual **address-decoder** fault: it changes which
physical cell an access lands on. The RTL computes an effective address `ea`
starting as the requested address, and for `AF_ALIAS`, reassigns
`ea = FQ[i].aa` (the fault's configured alias target) before the read/write
proceeds — `fault_ram.sv`'s `write_op`/`read_op`: *"write/read lands on alias
target."* So an access aimed at the injected `vaddr` is silently redirected to
`aaddr`, and it is `aaddr` (not `vaddr`) where the algorithm actually observes any
resulting mismatch, across every bit of that word (the whole word is redirected,
not a single bit — hence `AF_ALIAS`'s observation rows spanning all `data_width`
bits at `aaddr` in the worked example above: injection at `addr=90, bit=0`,
observation at `addr=91, bit=0..7`).

The practical takeaway: if you're using the diagnosis table to hunt for
address-decoder problems, expect and look for injection/observation-site
divergence (`role: injection` at one address, matching `role: observation` rows
at a different address). If you're diagnosing a coupling-fault escape, do not
expect to find its observation elsewhere in the table — a coupling fault that
escapes will show up as `role: injection` (or simply absent, if truly nothing
ever touched that cell) at its own victim cell, and nowhere else, because this
engine has no mechanism by which a coupling fault could ever be observed at a
different address than where it was injected.

---

## 5. Scope and limitations

- The algo-shell `--diagnosis` report is defined for a **single** campaign
  result. It has no defined shape for a multi-algorithm comparison
  (`compare_algo` in the shell, or comparing several `--report` runs) — request
  it per-algorithm instead.
- The diagnosis table is a snapshot of one fault list against one algorithm on
  one memory geometry; it is not a substitute for the coverage-matrix reports
  (`render_matrix_*` in `algo_reporting.py`) when you want a side-by-side
  algorithm comparison — those remain the right tool for that question.
- `fault_details` reflects whatever the underlying cocotb testbench printed as
  `FAULT_SITE` lines for that run's selected fault model; it is not retroactively
  computed from any other report, so it can only be as complete as the
  testbench's own stdout capture (interleaved or truncated stdout on a timeout
  means some sites may be missing from the array, not an error condition).
