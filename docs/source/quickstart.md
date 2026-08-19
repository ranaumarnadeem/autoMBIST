# Quickstart

This walks **one memory design** through both of autoMBIST's entry points,
one step at a time: the classic path (generate RTL, simulate it) and the
research path (grade march algorithms against the fault model). Every
command below was run against the exact scaffold `autombist init` produces,
so there is nothing to edit first.

```bash
# Inside WSL/Linux, from the repo root (see Installation):
nix develop
```

## Part 1 — Classic path: generate and simulate a real wrapper

### Step 1. Scaffold a starter project

```bash
autombist init --out .
```

```text
Created starter MBIST config: config.yml
Created starter OpenRAM config: openram.yml
Created starter Makefile: Makefile
Created sample SRAM model: sram_1rw.v
```

`config.yml` describes a 10-bit-address, 32-bit-data single-port memory
named `sram_1rw`:

```yaml
memory_name: sram_1rw
wrapper_module_name: sram_1rw_mbist
addr_width: 10
data_width: 32
we_active_low: true
ports:
  clk: clk0
  addr: addr0
  din: din0
  dout: dout0
  we: we0
  csb: csb0
```

`sram_1rw.v` is a plain behavioral RAM matching those ports — a stand-in for
your own OpenRAM-generated or vendor macro. Everything below runs against it
unmodified.

### Step 2. Generate the MBIST wrapper

```bash
autombist generate --config config.yml --out out
```

```text
Generated MBIST wrapper: out/sram_1rw/sram_1rw_mbist.v
```

This also writes the march-C controller RTL and repair-logic modules
alongside it under `out/sram_1rw/`. `<memory_name>` (`sram_1rw` here) is the
config's `memory_name` field — each memory gets its own subdirectory, so
`generate` can be re-run for several memories into the same `out/`.

### Step 3. Simulate it

```bash
autombist simulate --out out/sram_1rw
```

```text
autombist: simulation PASS
  memory: sram_1rw
  algo: march-c
  fault type: stuck-at
  runtime: 5.6s
  log: out/sram_1rw/simulate.log
  report: out/sram_1rw/reports/latest.json
  coverage: not reported by the simulator
  junit: 5 tests, 0 failures, 0 errors
```

`coverage: not reported` is expected here — this run injected no faults, so
there is nothing to score. That comes next.

### Step 4. Regenerate with fault injection

```bash
autombist generate --config config.yml --out out \
    --test --faults 50 --seed 1234 --algo march-c --fault-type stuck-at
autombist simulate --out out/sram_1rw
```

```text
autombist: simulation PASS
  ...
  coverage: 50/50 (100.00%)
  injected faults: 50
  junit: 5 tests, 0 failures, 0 errors
```

`--seed` makes the injected fault set reproducible. `out/sram_1rw/reports/latest.json`
now holds the full per-fault detail (address, bit, fault type, detected/escaped)
behind that summary line — machine-readable, for anything downstream that
wants the raw data rather than the printed report.

:::{note}
`--fault-type stuck-at` is the classic path's built-in mask-fault injector —
deliberately simpler than the research path's fault model below. It answers
"does this generated RTL detect stuck-at/transition faults," not "how does
march-C compare to march-SS across 31 fault primitives." For the latter, use
Part 2.
:::

## Part 2 — Research path: grade march algorithms, same design shape

No macro is required here — `autombist algo` drives a behavioral,
fault-injectable RAM directly. This section grades algorithms against a
10-bit-address, 32-bit-data, single-port memory: the same shape Part 1 just
generated RTL for, so the two parts describe one design end to end.

### Step 1. Open the shell and describe the memory

```bash
autombist algo
```

```text
algo> set_memory 10 32
memory set: 10x32, init=1, ports=1, words_per_row=1
```

### Step 2. Generate a fault list

```text
algo> gen_faults --all-types
generated 30 faults
```

30, not 31 or 29 — see {doc}`algo-shell-guide` for why the model, the
default list, and what a given memory configuration actually gets differ.
Here the memory is single-port, which is exactly the condition that admits
the data retention fault (`DRF`); `words_per_row` is 1 (the default), which
is what excludes half-select disturb (`HSD`). Both are config-gated, not
omitted by oversight.

### Step 3. Grade an algorithm

```text
algo> run march_c
march_c: 21/30 detected (70.00%)  build=15.1s run=0.1s
```

### Step 4. Compare it against others

```text
algo> compare_algo march_c -march march_ss,march_b
```

```text
| fault           | march_c | march_ss | march_b |
| --------------- | ------- | -------- | ------- |
| SA0@3.0         | D       | D        | D       |
| WDF0@31.4       | E       | D        | E       |
| DRDF0@59.8      | E       | D        | E       |
| SOF@87.12       | E       | E        | D       |
| CFWD0@150.21    | E       | D        | E       |
| CFRD0@164.23    | D       | D        | E       |
| CFDRD0@192.27   | E       | D        | E       |
| DRF@206.29      | D       | D        | D       |
| ...             |         |          |         |
| total           | 21/30   | 29/30    | 20/30   |
```

(Rows abbreviated here; the real output lists all 30.) March-C never
performs a non-transition write, so it misses `WDF`; March-SS's superset
construction catches it. March-B is the only one of the three that detects
`SOF` — the same measured trade-offs documented in
{doc}`algo-shell-guide`'s full coverage table.

### The scriptable equivalent

Everything above also runs non-interactively, which is what CI and
one-shot grading actually use:

```bash
printf 'set_memory 10 32\ngen_faults --all-types\nexport_tb bundle\nquit\n' \
  | autombist algo --script -
autombist test -aw 10 -dw 32 --algo march_c --faults bundle/faults.txt
```

```text
autombist test: march_c (10n) on 10x32 memory, init=1
  faults: 30   detected: 21   coverage: 70.00%
```

`export_tb` writes the session's current fault list to `bundle/faults.txt`
(alongside a runnable standalone testbench); `autombist test` is the
single-command form of steps 1–3 above and reports the identical result —
21/30, 70.00% — confirming the interactive shell and the scripted CLI agree
exactly rather than being two independently-implemented paths that happen to
usually match.

## Next steps

- Have a real memory macro to wrap? {doc}`example` walks through the full
  three-macro subsystem, including redundancy repair.
- Want to harden a design to GDS? See {doc}`librelane`.
- Full flag-by-flag reference: {doc}`cli-reference`.
- Full fault-primitive semantics and every built-in algorithm's measured
  coverage: {doc}`algo-shell-guide`.
