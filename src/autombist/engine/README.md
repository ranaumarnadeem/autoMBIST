# mbist_faultlib

Fault-injectable behavioral RAM plus a March algorithm runner and a serial
fault campaign driver, for developing and validating MBIST algorithms.
Runs unmodified under Xcelium (xrun) and Verilator 5.x.

## Files

    fault_ram.sv          fault-injectable RAM (the model); num_ports=1 (single-port,
                           default) or num_ports=2 (rendered via fault_ram_gen.py --
                           see "Multi-port" below)
    openram_shim.sv        drop-in wrapper matching the OpenRAM 1rw pinout
    openram_shim_mp.sv     num_ports=2 counterpart of openram_shim.sv -- one shared
                           fault_ram core across both port buses (see "Multi-port" below)
    march_engine.sv        MATS+, March C-, March SS runner with detection attribution
                           (single-port only; never touched by the multi-port work)
    march_engine_mp.sv     num_ports=2 counterpart of march_engine.sv -- same file-driven
                           .alg + fault-list grammar, extended with a port-suffix/column
                           for genuine cross-port coupling (see "Multi-port" below)
    faults.example.txt    one instance of every implemented fault primitive
    run_campaign.sh       serial campaign: one sim per fault, CSV out

## Quick start

Xcelium, single fault:

    xrun -64bit -sv fault_ram.sv march_engine.sv \
        +ALG=MARCHCM +FAULTS=faults.example.txt +FAULT_INDEX=6

Verilator:

    verilator --binary --timing -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND \
        --top-module march_engine fault_ram.sv march_engine.sv -o march_engine_sim
    ./obj_dir/march_engine_sim +ALG=MARCHSS +FAULTS=faults.example.txt +FAULT_INDEX=8

Full campaign (auto-detects xrun, else Verilator; force with SIM=):

    ./run_campaign.sh faults.example.txt MARCHCM

Each run prints exactly one RESULT line:

    RESULT DETECTED alg=MARCHCM elem=1 op=0 addr=50 xor=00000100
    RESULT ESCAPED  alg=MARCHCM

elem/op index into the March algorithm (element, operation within element),
which gives you exact detection attribution per fault.

## Fault list format

One fault per line, all fields decimal, `#` comments:

    TYPE  VADDR VBIT  AADDR ABIT  P0 P1  [VPORT APORT]

Victim = the cell whose stored value or read value is corrupted.
Aggressor = the acting cell (coupling faults) or the alias target (AF_ALIAS).
Unused fields must still be present; write 0.

The trailing `VPORT APORT` columns are optional (9 fields total instead of
7) and only meaningful against a `num_ports=2` `fault_ram.sv` / `march_engine_mp.sv`
-- see "Multi-port" below. Omitting them (every pre-multi-port fault list,
including `faults.example.txt`) means `VPORT=APORT=0`, so every existing
7-field fault-list file parses unchanged under either engine.

## Multi-port (`march_engine_mp.sv`, `num_ports=2`)

Everything above (files, quick start, fault list format, semantics table)
describes the default, single-port engine (`march_engine.sv` + a
`num_ports=1` `fault_ram.sv`), which is untouched by the multi-port work and
remains every existing caller's default. `march_engine_mp.sv` is a second,
independent testbench for a `num_ports=2` `fault_ram.sv` (rendered via
`fault_ram_gen.render_and_write(..., num_ports=2)`), used only when
`MemoryParams.num_ports == 2` (`autombist algo`'s `set_memory --ports 2`, or
`run_algo_campaign`/`run_fsm_campaign`'s dispatch in `algo_engine.py`).

### Port-tagged `.alg` op syntax

An op token in a `.alg` file may carry an optional `.PORT` suffix -- `r0.1`,
`w1.0`, etc. -- meaning "issue this op on port `PORT`" (`PORT` in `{0, 1}`).
Omitting the suffix (every built-in `.alg` file, and every op in a
single-port campaign) means port 0, so a plain `.alg` file's meaning is
unchanged under `num_ports=2`:

    either w0            # init, port 0 (implicit)
    up   w1.1             # up-transition write to every word, on port 1
    up   r1               # read every word, on port 0 (implicit)

`AlgSpec.to_numeric()`/`Element.numeric_line()` emit the plain `DIR NOPS
OP0..OP7` numeric format (byte-identical to pre-multi-port output) when
every op in the spec is on port 0, and only switch to the extended format
(additional trailing `PORT0..PORT7` columns, with a `PORT0..PORT7` header
suffix) when a non-zero port is actually used somewhere in the spec.

### 9-field fault-list format and coupling semantics

The `VPORT`/`APORT` columns select which physical port the victim/aggressor
side of a fault is sensed/triggered on. They are meaningful only for the
four coupling-class primitives (CFIN, CFID, CFST, CFDS) -- every other
primitive's semantics are unaffected by which port is used (a static clamp,
transition fault, etc. is the same fault regardless of access port).

- **Same-port coupling** (`VPORT == APORT`, or both omitted/0): the
  aggressor's sensitizing op and the victim's disturbance are both
  evaluated against the same physical port -- today's only pre-multi-port
  mode, reproduced unchanged through `march_engine_mp.sv` in its degenerate
  single-port use.
- **Cross-port coupling** (`VPORT != APORT`): the aggressor op is issued on
  a *different* physical port than the one used to sense the victim -- e.g.
  a write on port 1 disturbing a cell later read back via port 0. This is
  the genuinely new capability `march_engine_mp.sv` adds: the aggressor-side
  match in `write_op()`'s aggressor loop checks the fault's `ap` (aport)
  field against the *actual issuing port* of the current op, so a fault
  only fires when the algorithm really does drive the claimed aggressor
  port -- a fault list that *says* cross-port but whose algorithm never
  issues on that port correctly ESCAPES rather than firing. See
  `tests/integration/test_march_engine_mp_cross_port_coupling.py` for the
  full differential proof (same-port detected, cross-port detected, and the
  escape control).

`autombist algo`'s `add_fault` command exposes `VPORT`/`APORT` directly
(`add_fault CFIN 5 1 6 1 2 0 0 1` defines a cross-port CFIN with aport=1),
and `set_memory --ports 2` configures the session for `march_engine_mp.sv`.

## Fault primitive semantics

Notation <S/F/R>: sensitizing op / faulty cell value / faulty read value.
This is van de Goor & Al-Ars's formal notation ("Functional Memory Faults:
A Formal Notation and a Taxonomy," VTS 2000).

| Type | Semantics |
|---|---|
| SA0, SA1 | victim bit held at 0/1 at all times, including init |
| TF0 | <0w1/0/->: up-transition write fails, bit stays 0 |
| TF1 | <1w0/1/->: down-transition write fails |
| WDF0 | <0w0/1/->: non-transition w0 flips the bit to 1 |
| WDF1 | <1w1/0/->: non-transition w1 flips the bit to 0 |
| RDF0 | <0r0/1/1>: read of 0 flips cell to 1 and returns 1 |
| RDF1 | <1r1/0/0>: symmetric |
| DRDF0 | <0r0/1/0>: read flips cell to 1 but returns the correct 0 |
| DRDF1 | <1r1/0/1>: symmetric |
| IRF0 | <0r0/0/1>: read returns 1, cell unchanged |
| IRF1 | <1r1/1/0>: symmetric |
| SOF | cell inaccessible: writes ignored, reads return the previous value on the dout bit (output keeper) |
| AF_NOACC | address decodes to no cell: writes dropped, reads return constant P0 on all bits |
| AF_ALIAS | accesses to VADDR land on word AADDR instead (decoder fault types II to IV, pairwise) |
| CFIN | aggressor transition (P0: 0=up, 1=down, 2=either) inverts victim bit |
| CFID | aggressor transition (P0 as above) forces victim bit to P1 |
| CFST | while aggressor bit holds state P0, victim bit is forced to P1 |
| CFDS | op on aggressor disturbs victim (invert). P0: 0=r0, 1=r1, 2=non-transition w0, 3=non-transition w1, 4=any read |

Multiple faults compose in file order; for clean attribution run serially
with +FAULT_INDEX (what run_campaign.sh does). +FAULT_VERBOSE prints
per-fault activation counts at end of sim, which separates
activated-but-unobserved from never-activated. Example: SOF under March C-
activates 10 times and still escapes.

## Measured results, faults.example.txt, INIT=1 (defaults)

| Fault | MATS+ (5n) | March C- (10n) | March SS (22n) |
|---|---|---|---|
| SA0, SA1 | D | D | D |
| TF0, TF1 | D | D | D |
| WDF0, WDF1 | E | E | D |
| RDF0, RDF1 | D | D | D |
| DRDF0, DRDF1 | E | E | D |
| IRF0, IRF1 | D | D | D |
| SOF | E | E | E |
| AF_NOACC, AF_ALIAS | D | D | D |
| CFIN | D | D | D |
| CFID | E | D | D |
| CFST | D | D | D |
| CFDS (any-read) | E | D | D |
| total | 12/19 | 14/19 | 18/19 |

These match the published coverage claims: March C- misses WDF (it never
performs a non-transition write) and DRDF (no read-after-read); March SS
adds both and covers all static simple faults. The MATS+ CFDS escape is a
double-inversion masking between its up and down passes. SOF escapes solid
data background March tests because the output keeper tracks neighboring
reads of the same expected value; detecting it needs consecutive reads of
opposite data (element-boundary cells, paused tests, or address-order
variants), so an SOF escape here is correct behavior, not a model bug.

## Semantics notes

Default memory init is 1 (+INIT to override). With a deterministic all-0
init, the initializing w0 element becomes a non-transition write and WDF0
fires at element 0, which misrepresents coverage: real silicon powers up
unknown, so WDF detection cannot rely on init state. Init=1 reproduces the
textbook escape/detect pattern.

Word background defaults to solid 0 / solid 1 (byte-identical to before this
was added), but `march_engine.sv`/`march_engine_mp.sv` accept
`+BACKGROUND=<DW-bit hex mask>`: a nominal w0/r0 drives/expects `mask` and
w1/r1 drives/expects `~mask`, via a single `bg_value()` function shared by
both the write side and the read-assertion side (so a fault-free run can
never spuriously diverge from itself under any mask). The Python campaign
driver's `run_background_campaign()`/`merge_background_results()`
(`algo_engine.py`) run the same algorithm/fault-list once per
`standard_backgrounds(data_width)` pattern (solid + `ceil(log2(W))`
column-stripe masks) and merge results as "detected if any background
caught it" -- exposed as `--backgrounds` on the shell's `run`/`compare_algo`
commands. Placing coupled pairs in different words on the same bit lane (as
`faults.example.txt` does) still works unmodified and needs no background at
all.

**Measured before/after delta:** `faults.example.txt`'s existing CFIN/CFID/
CFST entries are all inter-word placements and are unaffected either way (by
design -- background only changes intra-word behavior). The concrete case
this closes: an intra-word `CFID` (`vaddr==aaddr`, victim and aggressor on
different bit lanes of the *same* word) with an "up"-transition sensitize
and a forced value that happens to coincide with what that same write op
would naturally produce for *every* bit of the word under a solid
background (since victim and aggressor are driven by the identical
instruction) -- e.g. `CFID vaddr=5 vbit=0 aaddr=5 abit=1 p0=0(up) p1=1` on
`march_c` at `+INIT=0` -- escapes under the solid background but is detected
once `--backgrounds` runs (verified via real Verilator runs, both with and
without the background loop; see
`tests/integration/test_data_backgrounds_e2e.py`).

Read fault evaluation uses the pre-read cell state; destructive read
effects land after the returned value is formed. Static clamps (SAF, CFST)
are re-applied after every operation, so they win over any coupling effect
targeting the same bit.

`march-raw`'s name does not claim identity with the literature's "March
RAW" test. Decoding `rtl/march_raw/march_raw_algo.sv`'s phase table gives
w0[1] / r0,w1,r1[3] / r1,w0,r0[3] x up and down = 14 operations/address
(14n), not the published March RAW's 26N -- a different length and, by
shape (a doubled/mirrored March-C--like read-write-read pattern), almost
certainly a different sequence. `march-raw` is this project's own name for
its own classic-path algorithm.

## Using with OpenRAM

openram_shim.sv matches the OpenRAM 1rw macro pinout
(clk0/csb0/web0/wmask0/addr0/din0/dout0) and expands the per-byte wmask0 to
the bit-level mask the core uses. Set DATA_WIDTH, ADDR_WIDTH, NUM_WMASKS to
the generated config and swap the instance. Golden runs against the real
OpenRAM Verilog model, fault runs against the shim, same testbench.

For a real 2-port (2RW) OpenRAM macro, use openram_shim_mp.sv instead: same
approach per port (clk0/csb0/.../dout0 and clk1/csb1/.../dout1), wrapping
one shared num_ports=2 fault_ram core so cross-port coupling stays
meaningful (see "Multi-port" above).

## Cadence notes

Xcelium is all this needs: plain SV, no PLI/DPI, no vendor constructs.
For campaign throughput, xrun recompiles per invocation; use
xrun -R with a saved snapshot, or run the elaboration once and loop only
the simulation, if the fault list gets large.

The Xcelium Safety App (fault simulator) is a separate licensed product
aimed at ISO 26262: it instruments net-level stuck-at and transient faults
in logic and runs serial/concurrent campaigns against a good-machine
reference. That is the right tool for measuring fault coverage of the MBIST
controller logic itself, but it does not model memory functional fault
primitives (coupling, destructive reads, decoder faults), which is why this
behavioral model exists. The two compose: this model validates the
algorithm, the safety app validates the controller.

If you later want to justify the fault list from silicon defects rather
than assume it: OpenRAM emits the full transistor-level SPICE netlist, so
resistive open/short/bridge injection in Spectre on the 6T cell plus
periphery, classified per read/write operation, derives which functional
fault primitives each defect maps to. That is the defect-oriented route and
turns an assumed fault list into a defensible one for a paper.
