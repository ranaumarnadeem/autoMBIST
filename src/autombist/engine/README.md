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
    faults.example.txt    one instance of every implemented fault primitive (41)
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

## Build cache (Python driver only)

The shell commands above always invoke `verilator` directly. `compile_engine`
in `algo_engine.py` -- the function every `run_algo_campaign`/
`run_background_campaign`/`run_fsm_campaign` call goes through, including the
CLI's `autombist test` and the Tcl/Python shells' `run`/`compare_algo` -- adds
a content-addressed build cache in front of it: the resolved source bytes
(after any custom-registry `fault_ram.sv` render) + top module + addr_width/
data_width/words_per_row + sim + `verilator --version` are hashed into a key,
and an identical key reuses the previously compiled binary instead of
recompiling. Compiling is the dominant cost of a campaign (seconds, vs.
milliseconds to run an already-built binary against one more fault), so a
research session or test run that repeats the same memory/algorithm/registry
combination -- which most do -- pays for the verilator build once, not once
per call.

Cached binaries live under a plain subdirectory of the OS temp dir by
default, persisting across separate process runs on one machine (not just
within a single campaign). Set `AUTOMBIST_ENGINE_CACHE` to a directory path
to relocate it, or to `0`/`off`/`false`/`no` to disable caching outright and
always rebuild, exactly like every call did before this existed. Every
`compile_engine`/`run_*_campaign` caller also accepts an explicit
`cache_dir=` argument, mainly useful for test isolation.

A different, complementary layer sits underneath: `flake.nix`'s devShell
exports `OBJCACHE=ccache` (verilator's own built-in hook for this, read
straight out of its generated `verilated.mk`), so even a cache MISS above
still gets a faster verilator build wherever a compiled translation unit --
verilator's own runtime library sources, shared across every design -- comes
out identical to a previous build. CI (`.github/workflows/test.yml`) persists
both `~/.cache/ccache` and `/tmp/autombist-engine-cache` across runs via
`actions/cache`, so this benefit isn't limited to one machine's local dev
loop.

## Fault-simulation concurrency

Once a campaign's binary is built (or reused from cache), the per-fault
simulation loop itself runs with bounded concurrency: `run_algo_campaign`,
`run_background_campaign`, and `run_fsm_campaign` all dispatch faults through
a `ThreadPoolExecutor` sized by `AUTOMBIST_FAULT_CONCURRENCY` (default 4).
Compilation stays single-threaded regardless of this setting -- only running
the already-built binary against each fault is parallelized. Set
`AUTOMBIST_FAULT_CONCURRENCY=1` to restore the original fully-sequential
behavior (useful when debugging a flaky fault or comparing timings against
older runs).

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

## Idle/wait op and Data Retention Fault (DRF)

An op token in a `.alg` file may also be `t<N>` (e.g. `t5`), meaning "idle
for N clock cycles, no memory access" -- for modeling faults sensitized by
elapsed idle time rather than any read/write sequence, rather than a real
memory access. It never takes a `.PORT` suffix (a wait touches no bus) and
does not count toward `AlgSpec.length_n` (the "operations per address"
complexity metric would be meaningless if idle cycles and access ops shared
a unit).

**Wait ops repeat once per address, like every other op.** A wait op sits
inside the same per-address loop as every read/write op, so it costs `N *
DEPTH` cycles total, not `N`: `either t5` on a depth-8 memory idles 5 cycles
at *each* of the 8 addresses (40 cycles total). Account for this
multiplication explicitly when sizing a wait-containing spec -- divide the
desired total by the memory's depth, or use a small/known depth.

DRF (Data Retention Fault) is the first fixed fault type sensitized by
elapsed idle time instead of an access sequence: after the victim cell's
last write, once `P0+1` idle clock edges have elapsed with no write to that
exact address, the victim bit inverts and stays inverted (silently, until
the next write to that address re-arms it). It's `P0+1`, not `P0` -- the
comparison checks the idle counter's value from the *prior* qualifying
edge before that edge's own increment, so corruption actually commits one
edge later than a naive reading of "P0 idle cycles" would suggest (measured
directly in simulation, not derived from the code shape alone -- see
`fault_ram.sv`'s idle-cycle-tracking `always_ff` block for the full
analysis). Corruption becomes visible on the next read of that cell, one
cycle after the threshold is crossed, the same "observe on next access"
characteristic as `fault_ram.sv`'s documented 1-cycle read latency, not a
bug. `P0` is reused directly as the idle-cycle threshold; `P1`/`AADDR`/
`ABIT` are unused (0). Detecting a DRF therefore requires a `.alg` spec
with an explicit wait element between the write that arms it and the read
that observes it -- none of the built-in march algorithms (MATS+, March B,
March C-, March SS, March X) contain a wait op, so DRF always escapes them
regardless of `P0` (this is expected, not a coverage gap: those algorithms
were never designed to test retention). `gen_faults --all-types` still
includes a DRF entry for single-port memories precisely so this shows up as
an honest ESCAPED result rather than being silently absent from the list.

**The idle counter runs from simulation time 0, not from a first arming
write.** There is no "wait for the first write before counting" gate --
`drf_idle_count` starts at 0 at power-up and accumulates from the very
first clock edge, exactly like it would after any other write to the
victim address. With a small `P0`, a campaign whose algorithm doesn't
write the victim address promptly (e.g. it reads or writes other addresses
first) can corrupt the cell before the intended write/wait/read sequence
ever runs. Always write the victim address explicitly before relying on a
wait-then-read pattern to test DRF.

**Single-port only in this phase.** DRF's idle-cycle tracking is a single
scalar register (`drf_idle_count`/`drf_corrupted`), matching the campaign
driver's one-active-fault-at-a-time (`+FAULT_INDEX`) discipline, but it is
not yet extended to `num_ports=2`. A fault list that actually loads a DRF
entry under a `num_ports=2` `fault_ram.sv` fails loud: the simulator prints
`FATAL: DRF is not yet supported for num_ports=2 ...` and `$finish`-es
rather than silently no-opping or running with wrong semantics. This guard
lives in `fault_ram_template.sv.j2` itself (fires only if a loaded fault
list actually contains a DRF entry), not in `fault_ram_gen.py`'s Python
code, since codegen time has no visibility into whether any given
campaign's fault *list* will ever use DRF.

## Checkerboard op (`wc`/`wcb`/`rc`/`rcb`)

Four op tokens in a `.alg` file are checkerboard-flavored: `wc`/`wcb` write,
`rc`/`rcb` read-and-expect, a value that is a function of the CURRENT
ADDRESS (`addr & 1` -- logical/LSB parity, not physical row/column
adjacency, which no consumer in this toolkit has geometry for) rather than
a fixed literal like `r0`/`r1`/`w0`/`w1`. `wcb`/`rcb` use the complement,
`~(addr & 1)`. See `checkerboard.alg` for the built-in that uses them.

Encoded as NEGATIVE op codes (`OP_WC=-1, OP_WCB=-2, OP_RC=-3, OP_RCB=-4` in
`alg_spec.py`) rather than extending past 3: codes >= `WAIT_BASE` (4) are
entirely claimed by the wait-op encoding above (`WAIT_BASE + N`, N up to
65535), leaving no free room above 3. Negative codes are backward compatible
by construction -- nothing existing assumes `op >= 0` -- and need no special
handling in the numeric serialization (`Element.numeric_line()`'s plain
`str(x)` emits a negative decimal exactly like a positive one, and both
engines' `$sscanf(..., "%d", ...)` parse a leading `-` natively).

`wc`/`wcb` accept the same `.PORT` suffix as any other op (`wc.1`, etc.) --
they are genuine memory ops, not a wait-like special case. `rc`/`rcb`
compose with `+BACKGROUND` exactly like `r0`/`r1` do: the single computed
parity bit still routes through `bg_value()`, the same per-campaign
data-background XOR every other op uses, so no engine-side special-casing
was needed beyond the dispatch arm itself.

## Half-Select Disturb (HSD)

A new fixed fault type sensitized by physical **row co-membership** rather
than a fixed `(aaddr, abit)` pair: activating a word line for one column's
access simultaneously stresses every other cell sharing that row, which can
disturb weakly-stable neighboring cells with no addressing error at all.
This is structurally novel relative to every other coupling primitive
(CFIN/CFID/CFST/CFDS all key on an arbitrary but fixed aggressor address) --
closest in spirit to the classical Neighborhood Pattern Sensitive Fault
(NPSF) family (a write to a physically neighboring cell disturbs a victim),
though HSD's "neighborhood" is an entire shared word line (potentially
hundreds to thousands of columns on a real macro), driven by a completely
different mechanism (word-line/access-transistor sharing, not layout-driven
capacitive proximity) -- a cousin of NPSF, not an instance of it.

**`words_per_row` and the row-membership test.** `MemoryParams.words_per_row`
(default 1; `set_memory --words-per-row N` on the shell) is this project's
existing name for "how many logical addresses share one physical row due to
column muxing" -- reused verbatim from `flow/multimem/mbist/README.md`'s
pre-existing repair-addressing correctness finding (verified there against
three real OpenRAM macros, all with `words_per_row == 1`, i.e. no muxing).
Two addresses are in the same physical row iff `row(a) == row(b)`, where
`row(addr) = addr / words_per_row` (integer division) -- a contiguous block
of `words_per_row` consecutive addresses per row, matching that same finding
(`ADDR_WIDTH == ceil(log2(words+spares))` is only self-consistent under a
contiguous-block grouping). `words_per_row` is a compile-time `WORDS_PER_ROW`
module parameter (`fault_ram`, `march_engine.sv`, `march_engine_mp.sv`), not
a per-fault field -- it describes a physical macro property, not a property
of any one fault instance.

**Effect: force toward a polarity, not invert.** `P0` (0 or 1) is the
*disturbed-toward* polarity; `AADDR`/`ABIT`/`P1` are unused (write 0, matching
SOF/AF_NOACC's convention -- HSD has no fixed aggressor address). A write to
any OTHER address sharing the victim's row forces the victim bit toward `P0`
(only counting as an activation if that actually changes the cell, the same
idiom SA0/SA1 already use). This is deliberately **not** an invert: real
half-select physics pulls a half-selected cell's storage node toward the
array's undriven/precharge polarity, not toward "whatever it currently
isn't" -- a cell already at that polarity is not disturbed further. Modeling
every qualifying disturb as firing deterministically (not probabilistically,
matching how every other coupling primitive here already fires
deterministically on every qualifying transition) is the least physically
realistic point on the real spectrum (real half-select disturb depends on
process/voltage/temperature and the specific cell's static-noise margin) --
useful for a fault-coverage figure, not a real silicon failure-rate estimate.

**Write-triggered only in this phase.** The modeled mechanism is write-driver
simultaneous-switching noise / word-line droop coupling into the row during a
write -- a real, write-specific stress mechanism, but not the only
half-select-adjacent mechanism in the literature (a separate
undriven-precharge-bitline mechanism doesn't obviously distinguish read from
write on the *accessed* column). Reads never trigger HSD in this phase; this
is a deliberate scope cut for the specifically-modeled mechanism, not a claim
that no read-based row-disturb mechanism exists anywhere in the literature.

**A same-row disturb only survives to be observed if it happens *after* the
victim's own most recent direct write in the traversal** -- a later direct
write to the victim always overwrites an earlier disturb (ordinary write
logic computes the new cell value unconditionally from the write data, with
no HSD-awareness). Confirmed directly in simulation, not assumed: at
`words_per_row=2` on a depth-4 memory, an HSD fault at `victim=addr3`
(row `{2,3}`, row-mate `addr2`) *escapes* an `up w0 / up r0` spec (`addr2`
is written *before* `addr3`'s own direct write in ascending order, so the
disturb is gone before it's ever read), while the identical setup at
`victim=addr0` (row-mate `addr1`, written *after* `addr0`'s direct write)
*detects*. This is real, expected physics (a disturb followed by a
legitimate rewrite is gone), not a bug.

**Fault-list line**: `HSD VADDR VBIT 0 0 P0 0` (e.g. `HSD 200 3 0 0 0 0`
disturbs bit 200.3 toward 0 whenever a different same-row address is
written). No `.alg` grammar change is needed -- HSD is sensitized purely by
ordinary write traffic a real march algorithm already generates, unlike DRF.

**Ships for `num_ports=2` in this same pass** (unlike DRF): the check is a
single, stateless per-fault comparison inside `write_op()`, which
`march_engine_mp.sv` already calls once per port against the SAME shared
`mem[]`/fault queue -- "physical row shared across both ports" (the
physically correct interpretation for a real dual-port SRAM's shared
bitcells) falls out for free, with no new register and no new insertion
site needed for the two-port case.

**Algo-front only in this phase.** `words_per_row != 1` is rejected for FSM
(`run_fsm_campaign`) targets -- only `march_engine.sv`/`march_engine_mp.sv`
expose a `WORDS_PER_ROW` top parameter to Verilator's `-G` override; the
generated FSM harness does not (a mechanical plumbing gap, not a modeling
question, unlike DRF's num_ports=2 deferral).

**`gen_faults --all-types`** includes HSD only when `words_per_row > 1`, and
DRF only when `num_ports == 1` (the same conditional-inclusion mechanism,
`_effective_all_types`, gates both -- see its own comment). HSD: at the
default, no row-mate address exists at all, so an included HSD entry would
always report zero hits; at `words_per_row > 1`, a real march algorithm's own
traversal detects it fine (confirmed directly against `march_c`), so
excluding it there would be an unnecessary gap rather than a real limitation.
DRF: excluding it whenever no BUILT-IN march algorithm has a wait op was
tried first and rejected -- that conflates "structurally undetectable"
(HSD's actual condition at the default) with "the algorithm about to run
doesn't happen to look for this" (true of every fault type here for some
algorithm; a coverage report is supposed to say so, not omit the fault).
`num_ports == 1` is the real, structural constraint: DRF's idle-cycle
tracking is a single scalar register not yet extended to `num_ports=2` (see
below), and a fault list that actually loads a DRF entry there fails loud
rather than silently.

**Provably inert at the default (`words_per_row=1`)**, exactly like DRF at
`num_ports=2`: `row(addr) = addr` for every address there, so "a different
address in the same row" is mathematically unsatisfiable. `add_fault`/
`load_faults` print a non-fatal WARNING (not a FATAL/`$finish`) when an HSD
entry is registered at `words_per_row<=1` -- unlike DRF's num_ports=2 case,
0 hits here is a *correct* result (there are genuinely no row-mates), not an
untrustworthy one, so a hard failure would be the wrong signal; the warning
exists purely to catch a likely-forgotten `--words-per-row` flag.

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
OP0..OP7` numeric format (byte-identical to pre-multi-port output for the
OP.../PORT... columns) when every op in the spec is on port 0, and only
switch to the extended format (additional trailing `PORT0..PORT7` columns,
with a `PORT0..PORT7` header suffix) when a non-zero port is actually used
somewhere in the spec.

The DIR column differs by entry point: `to_numeric()` resolves any `either`
element to a concrete up/down first (`alg_spec.resolve_directions()` -- see
"How `either` gets resolved" in the algo-shell guide), so the `.algc` file the
engine reads never contains a literal `2`. Calling `Element.numeric_line()`
directly, without going through `to_numeric()`, instead emits that element's
`.direction` verbatim -- `either` (2) included.

### 9-field fault-list format and coupling semantics

The `APORT` column selects which physical port the **aggressor** side of a
fault is triggered on. `VPORT` is parsed but **not yet honoured** -- see the
subsection below.

`APORT` is meaningful only for the coupling-class primitives, and only for
**three** of the four: CFIN and CFID (matched in `write_op()`'s aggressor
loop) and CFDS (matched in both loops). **CFST does not honour it** -- CFST
is a static clamp, so its arm is emitted into `clamp_static()`, which
re-asserts stored state on every access and takes no `port` argument, leaving
nowhere for a per-port gate to sit. Every non-coupling primitive is
port-agnostic by construction (a transition fault is the same fault
regardless of access port).

- **Same-port coupling** (`APORT` omitted/0): the aggressor's sensitizing op
  is evaluated against port 0 -- today's only pre-multi-port mode, reproduced
  unchanged through `march_engine_mp.sv` in its degenerate single-port use.
- **Cross-port coupling** (`APORT` naming the other port): the aggressor op is
  issued on a *different* physical port than the one used to sense the victim
  -- e.g. a write on port 1 disturbing a cell later read back via port 0. This
  is the genuinely new capability `march_engine_mp.sv` adds: the aggressor-side
  match in `write_op()`'s aggressor loop checks the fault's `ap` (aport)
  field against the *actual issuing port* of the current op, so a fault
  only fires when the algorithm really does drive the claimed aggressor
  port -- a fault list that *says* cross-port but whose algorithm never
  issues on that port correctly ESCAPES rather than firing. See
  `tests/integration/test_march_engine_mp_cross_port_coupling.py` for the
  full differential proof (same-port detected, cross-port detected, and the
  escape control).

#### `VPORT` is reserved, not load-bearing

`VPORT` is parsed into the engine's `FQ[i].vp` field and carried through
`FaultRecord`, but **no expression in the generated module reads it**. The
victim-side guards in `write_op()`/`read_op()` match on address and bit alone.
Setting `VPORT` therefore changes nothing for any fault type today; it is
reserved for a future per-port victim gate. The cross-port test above fixes
`vport` at 0 throughout and varies only `aport`, for exactly this reason.

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
| CFTR0 | <a; 0w1/0/->: aggressor holding P0 blocks the victim's up-transition write |
| CFTR1 | <a; 1w0/1/->: symmetric, down-transition |
| CFWD0 | <a; 0w0/1/->: aggressor holding P0 turns the victim's non-transition w0 into a flip to 1 |
| CFWD1 | <a; 1w1/0/->: symmetric |
| CFRD0 | <a; 0r0/1/1>: aggressor holding P0 turns a read of 0 into a flip-and-return-1 (RDF, gated) |
| CFRD1 | <a; 1r1/0/0>: symmetric |
| CFIR0 | <a; 0r0/0/1>: aggressor holding P0 turns a read of 0 into a lying 1 with the cell unchanged (IRF, gated) |
| CFIR1 | <a; 1r1/1/0>: symmetric |
| CFDRD0 | <a; 0r0/1/0>: aggressor holding P0 flips the cell to 1 but the read still (deceptively) returns 0 (DRDF, gated) |
| CFDRD1 | <a; 1r1/0/1>: symmetric |
| DYN_RDF00 | <0w0r0/1/1>: a non-transition w0 immediately followed by a read flips the cell to 1 and returns 1 |
| DYN_RDF01 | <0w1r1/0/0>: symmetric, transition write |
| DYN_RDF10 | <1w0r0/1/1>: symmetric |
| DYN_RDF11 | <1w1r1/0/0>: symmetric |
| DYN_DRDF00 | <0w0r0/1/0>: the same adjacency flips the cell to 1 but the read still (deceptively) returns 0 |
| DYN_DRDF01 | <0w1r1/0/1>: symmetric |
| DYN_DRDF10 | <1w0r0/1/0>: symmetric |
| DYN_DRDF11 | <1w1r1/0/1>: symmetric |
| DYN_IRF00 | <0w0r0/0/1>: the same adjacency returns 1 with the cell left unchanged |
| DYN_IRF01 | <0w1r1/1/0>: symmetric |
| DYN_IRF10 | <1w0r0/0/1>: symmetric |
| DYN_IRF11 | <1w1r1/1/0>: symmetric |
| DRF | victim bit inverts after P0 idle cycles since its last write, no access needed (see "Idle/wait op" above); single-port only |
| HSD | victim bit forced toward P0 whenever a DIFFERENT address sharing its physical row (row = addr/words_per_row) is written (see "Half-Select Disturb" above); provably inert at the default words_per_row=1 |

CFTR/CFWD/CFRD/CFIR/CFDRD extend the notation to two cells, `<Sa; Sv/F/R>`
(Sa the aggressor's held state, Sv/F/R the victim's own sensitizing op /
faulty value / faulty read, exactly as DATE 2006 Table 2 itself states it;
this repo's TF0 is that paper's TF1, a pre-existing naming divergence --
every other name matches its source paper directly). DYN_RDF/DRDF/IRF's `S`
is the two-operation `xwyry` sequence itself (a write immediately followed
by a read of the same cell, no intervening access) rather than a single op
-- VTS 2002's own dFFM`<x><y>` convention for this family, which this
repo's naming already follows; see "Dynamic (2-operation) faults" below.

Multiple faults compose in file order; for clean attribution run serially
with +FAULT_INDEX (what run_campaign.sh does). +FAULT_VERBOSE prints
per-fault activation counts at end of sim, which separates
activated-but-unobserved from never-activated. Example: SOF under March C-
activates 10 times and still escapes.

## Measured results, faults.example.txt, INIT=1 (defaults)

41 faults: one instance of each of the 19 single-cell/decoder/coupling
primitives, the ten two-cell coupling types, and the twelve single-cell
dynamic (2-operation) types (see "Dynamic (2-operation) faults" below).

| Fault | MATS+ (5n) | March Y (8n) | March C- (10n) | March C+ (14n) | March B (17n) | March SS (22n) |
|---|---|---|---|---|---|---|
| SA0, SA1 | D | D | D | D | D | D |
| TF0, TF1 | D | D | D | D | D | D |
| WDF0, WDF1 | E | E | E | E | E | D |
| RDF0, RDF1 | D | D | D | D | D | D |
| DRDF0, DRDF1 | E | D | E | D | E | D |
| IRF0, IRF1 | D | D | D | D | D | D |
| SOF | E | D | E | D | **D** | E |
| AF_NOACC, AF_ALIAS | D | D | D | D | D | D |
| CFIN | D | D | D | D | D | D |
| CFID | E | E | D | D | D | D |
| CFST | D | D | D | D | D | D |
| CFDS (any-read) | E | D | D | D | D | D |
| CFTR0, CFTR1 | D/E | D/E | D | D | D | D |
| CFWD0, CFWD1 | E | E | E | E | E | D |
| CFRD0, CFRD1 | E | E | D | D | D/E | D |
| CFIR0, CFIR1 | E | E | D | D | D/E | D |
| CFDRD0, CFDRD1 | E | E | E | D | E | D |
| DYN_RDF00, DYN_RDF11 | E | E | E | E | E | D |
| DYN_RDF01, DYN_RDF10 | E | D | E | D | D | E |
| DYN_DRDF00, DYN_DRDF11 | E | E | E | E | E | E |
| DYN_DRDF01, DYN_DRDF10 | E | D | E | D | E | E |
| DYN_IRF00, DYN_IRF11 | E | E | E | E | E | D |
| DYN_IRF01, DYN_IRF10 | E | D | E | D | D | E |
| total | 13/41 | 23/41 | 20/41 | 31/41 | 23/41 | 32/41 |

These match the published coverage claims: March C- misses WDF (it never
performs a non-transition write) and DRDF (no read-after-read); March SS
adds both and covers all static simple faults *in this fault list*. Every
count above is a real `run_algo_campaign` result (`golden_clean=True` on
every row), cross-checked against `synth_engine.py`'s independent pure-Python
`(v, a)` replay oracle for all 12 dynamic types across all six algorithms (72
cells, zero mismatches) -- two independently-coded implementations of the
same spec agreeing, not one figure copied from the other.

## Dynamic (2-operation) faults

The twelve `DYN_RDF/DRDF/IRF` × `00/01/10/11` types are **not** in DATE 2006.
That paper's Table 1 is single-cell *static* FFMs and Table 2 is two-cell
*static* FFMs; the word "dynamic" appears in it only inside a reference
title. The dynamic space comes from Hamdioui, Al-Ars & van de Goor, "Testing
Static and Dynamic Faults in Random Access Memories", VTS 2002 (extended as
JETTA 19(2), 2003). Restricted to its SPICE-validated sequence S = xwyry
(a write immediately followed by a read of the *same* cell, no intervening
access to it), that space is 12 single-cell FPs (dRDF, dDRDF, dIRF) and 32
two-cell FPs (dCFds, dCFrd, dCFdrd, dCFir) -- this model implements the 12
single-cell ones; the two-cell dynamic types are deferred (v1 scope cut, on
measurement not principle). It contains no dTF and no dWDF, which come from
the later ETS 2005 space.

Each name's trailing two digits are the sensitizing write's own transition,
read left-to-right as `(cell value immediately before the write)(value
written)` -- `01` senses a 0-then-write-1 (transition) adjacency, `00` a
0-then-write-0 (non-transition) one, and so on. **The measured split is
governed entirely by that digit pair, not by algorithm length**: every
algorithm here either detects the *same-polarity* pair (`*00`/`*11`) or the
*opposite-polarity* pair (`*01`/`*10`), never a mix, because which pair an
algorithm can even sensitize is fixed by which write transitions its own
`.alg` element text issues immediately before a same-address read --
`resolve_algo("march_y").elements`, not the fault list, decides this.
March SS's extra `r0 r0 w0 r0 w1`-shaped elements create only non-transition
write-then-read adjacencies (`*00`/`*11`); March Y/March C+/March B's
`r0 w1 r1`-shaped elements create only transition ones (`*01`/`*10`); March
C-, MATS+, and March X issue no same-address write-immediately-followed-by-
read adjacency at all (every write in their elements is followed by a
*different*-address op before that cell is read again), so they detect none
of the 12 regardless of polarity.

**March B detects DYN_RDF and DYN_IRF at `*01`/`*10` but not DYN_DRDF.** DRDF
is deceptive by definition -- the sensitizing read itself still returns the
correct value, so observing the corruption needs a *later* read of the same
cell before any intervening write. March Y and March C+ get this for free
(their very next element re-visits the same address read-first, in the
opposite sweep direction); March B's analogous element moves on to a
different address, so the corrupted cell is silently overwritten before
March B ever reads it again. Confirmed against `synth_engine.py`'s oracle,
not assumed from the `.alg` text alone.

**Why 41 and not 43.** The model has 43 primitives; a default fault list has
41. DRF and HSD are the difference, and they are excluded by *configuration*,
not by omission: DRF needs a `wait` op and a single-port memory to sensitize,
HSD needs `words_per_row > 1`. `gen_faults --all-types` adds each only when
the memory supports it, so the totals below are out of 41 for this memory.
Quote coverage as "N/41 against faults.example.txt", never as "N of the
model". The MATS+ CFDS escape is a
double-inversion masking between its up and down passes.

**March Y (8n)** is March C- reshaped, not shortened for free: it trades a
DRDF/SOF-exposing read-after-read at each visited cell (`up r0 w1 r1` / `down
r1 w0 r0`) for one of March C-'s two up-transition writes (element 4's `down
r0 w1` is gone), which is exactly the write March C- uses to catch CFID from
above -- and that same swap is what creates the `*01`/`*10` dynamic
adjacencies March C- never issues. Net across all 41: +3 (nine gained --
DRDF0/DRDF1/SOF plus six of the twelve dynamic types -- against six lost:
CFID and five other two-cell coupling types March C-'s extra write still
catches). Against the 29 static/coupling-only entries alone the swap is a
net *loss* of 3 (17/29 vs 20/29) -- the dynamic family is the entire reason
March Y's full-list standing is positive rather than negative.

**March C+ (14n)** is the un-reduced original March C that March C- is
van de Goor's reduction of -- putting back the trailing verify-read of each
middle element March C- drops. Strictly better than March C- on every fault
this table tracks (+11, zero regressions): the restored reads create the same
same-address, no-intervening-write double-read March Y's shorter form relies
on for DRDF/SOF and for six of the twelve dynamic types, without giving up
March C-'s CFID-catching write.

**March B's standing reversed twice as the fault list grew.** Against the
original 19-fault list it beat March C- (15/19 vs 14/19) on the strength of
SOF; against the 29-entry static+coupling list it trailed (19/29 vs 20/29),
because March C- catches six of the ten two-cell coupling types to March B's
four; against the full 41-entry list with dynamic faults it leads again
(23/41 vs 20/41), picking up four of the twelve dynamic types (DYN_RDF and
DYN_IRF at `*01`/`*10`, see above) that March C- structurally cannot reach at
all. The SOF advantage itself is unchanged and still real — element 2 (`up
r0 w1 r1 w0 r0 w1`) reads the same cell at opposite polarity within a single
address visit, so the output keeper an SOF models is forced across reads of
opposite expected data, which is precisely the condition described below as
necessary to expose it. No other built-in does that. But the reason to reach
for March B is *linked* coupling faults (one coupling fault masking another),
and `faults.example.txt` contains no linked faults at all — so the property
it is actually chosen for contributes nothing to this table. Read its 15/19
as a lower bound on a fault list that cannot test its real strength, not as
the whole value of 7 extra operations over March C-.

SOF escapes solid
data background March tests because the output keeper tracks neighboring
reads of the same expected value; detecting it needs consecutive reads of
opposite data (element-boundary cells, paused tests, or address-order
variants), so an SOF escape here is correct behavior, not a model bug.

DRF is not in `faults.example.txt` and not in this table: detecting it needs
a `.alg` spec with an explicit wait element (see "Idle/wait op" above), which
none of MATS+/March C-/March SS contain -- against any of them it would
escape unconditionally, which would misrepresent the table's per-algorithm
coverage comparison rather than illustrate anything about DRF itself.

HSD is not in `faults.example.txt` and not in this table either, for a
different reason than DRF: this table's campaign runs at the default
`words_per_row=1`, at which HSD is provably inert (see "Half-Select Disturb"
above) -- including it here would show a universal escape that says nothing
about HSD itself, only about the memory configuration this table happens to
use. `gen_faults --all-types` includes HSD automatically once
`words_per_row > 1` is configured (see that section).

## Limits

**Only S = xwyry is modeled.** VTS 2002's own SPICE analysis (Section 4)
validated exactly one sensitizing sequence -- a write immediately followed
by a read of the same cell, no intervening access of any kind -- and left
broader sequences (`rxrx`, `rxwy`) as the paper's own open question, not an
oversight of this implementation. "No intervening access" is enforced here
by a single SHARED last-operation record (`lop_w`/`lop_a`/`lop_pre`/`lop_m`/
`lop_d` in `fault_ram.sv`, `last_op` in `synth_engine.py`'s oracle) that
EVERY `write_op()`/`read_op()` call -- to any address, on any port --
invalidates unconditionally on entry (`read_op()` snapshots it into locals
first, since only reads evaluate a `sensitize.prev` condition; `write_op()`
just invalidates, then re-arms it with its own address/data once the write
genuinely commits): only the operation *literally immediately preceding*
the read, anywhere in the memory, can ever be "the last op." This was a
measured design choice, not the obvious one: a
per-port record and a per-victim-address record were both tried first and
found wrong, in the same way -- an intervening write from a *different*
port (or to the same address via a different port) must still be visible to
the read that follows it, which a record scoped to "this port" or "this
address alone" cannot represent on its own. A wait op is a genuine
exception, not a narrower-record workaround: `w0 t5 r0` (a write, an idle
wait, then a read) still sensitizes a same-polarity dynamic fault, because a
wait touches no bus and never calls `write_op`/`read_op` at all (see
"Idle/wait op" above) -- the shared record simply never sees it. Any other
intervening op, to any address or port, does invalidate the adjacency.

**The two-cell dynamic types are not implemented.** VTS 2002's own dynamic
space also includes dCFds/dCFrd/dCFdrd/dCFir -- 32 FPs, an aggressor-gated
version of the twelve single-cell types here, the same way
CFTR/CFWD/CFRD/CFIR/CFDRD gate the static RDF/WDF/etc. family on an
aggressor's held state. They are a deliberate v1 scope cut, on measurement
rather than principle: nothing about the
`sensitize.prev`/`lop_*` mechanism structurally prevents combining it with
`agg_pre`, but doing so correctly needs its own bidirectional-placement
treatment (the same aggressor-above/aggressor-below soundness requirement
:func:`synthesize_elements` already enforces for the static two-cell family,
extended to a sequence-sensitive victim condition) that has not been
designed or measured yet.

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
