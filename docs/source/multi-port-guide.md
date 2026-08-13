---
orphan: true
---

# Multi-Port Memory Guide

A practical guide to testing 2-port memories with autoMBIST — dual-port and
1R1W (1-read-port + 1-write-port) SRAMs — across both of autoMBIST's
subsystems, plus a third, separate multi-port surface for researchers
validating their own controller FSM.

This guide assumes you already know the basics of generating/simulating a
single-port memory (see the main [README](https://github.com/ranaumarnadeem/autoMBIST/blob/main/README.md)) or of running the
algo-shell (see `src/autombist/engine/README.md`). It focuses only on what
changes when your memory under test has two physical ports.

## 1. What "multi-port" means here, and why it matters

autoMBIST recognizes exactly two 2-port topologies:

- **1R1W** — one **read-only** port and one **write-only** port. This models
  memories where the read and write sides are physically separate buses
  (common in dual-port SRAM macros used for FIFOs, register files, and
  pipeline buffering).
- **2RW** — two **fully symmetric** read/write ports. Either port can
  independently read or write on any cycle. This models genuine dual-port
  SRAMs where both ports have the complete pin set (`clk`/`addr`/`din`/
  `dout`/`we`/`csb`).

Both are real dual-port SRAM shapes you'll find in OpenRAM-generated macros
and commercial memory compilers — this isn't a synthetic testing construct.

The reason multi-port support exists at all is **port-coupling faults**: a
defect where an access on one port (the *aggressor*) disturbs a cell that is
concurrently accessed via the *other* port (the *victim*). A "test each port
separately" approach is structurally blind to this class of defect — you only
observe it if the aggressor and victim accesses are issued on genuinely
different ports on the same cycle, and the underlying fault model actually
shares state between them (see [the shared-array note](#5-the-shared-array-architectural-note),
which is non-negotiable for this to mean anything).

autoMBIST exposes two independent, purpose-built March algorithms for this:

- **march-1r1w** exploits the 1R1W topology's concurrency directly: the read
  port and write port issue to the *same address on the same clock edge*,
  which is exactly the condition needed to catch inter-port bridging defects.
- **march-2rw** generalizes further to two fully symmetric read/write ports,
  letting the algorithm exercise access patterns march-1r1w structurally
  cannot express — concurrent write/write to two *different* addresses, and
  concurrent read/read to the *same* address — on top of the same
  read(one port)/write(other port) same-address case march-1r1w already
  covers.

## 2. Classic path: configuring and generating a multi-port memory

The classic path is `autombist generate` / `autombist simulate` — the
Cocotb + Icarus Verilog flow that wraps your memory in a saboteur/MBIST RTL
harness and injects array-level stuck-at/transition/port-coupling faults.

### 2a. The `ports:` config block

A single-port `config.yml` describes its pins with a flat 6-key `ports:`
dict (`clk`, `addr`, `din`, `dout`, `we`, `csb`). A multi-port memory instead
uses a **named** `ports:` map: each key is a port name you choose, and each
value is a mapping with a `type` field plus that type's required signal keys.

| `type` | Meaning | Required keys |
|---|---|---|
| `rw` | full read/write port (the legacy single-port shape, and both ports of a 2RW memory) | `clk`, `addr`, `din`, `dout`, `we`, `csb` |
| `r` | read-only port | `clk`, `addr`, `dout`, `csb` |
| `w` | write-only port | `clk`, `addr`, `din`, `csb`, `we` |

Which algo you select (`--algo march-1r1w` or `--algo march-2rw`) determines
which port *composition* is required, and this is enforced at generate time:

- `march-1r1w` requires **exactly one `r` port and one `w` port** — any
  other count or type combination is rejected (e.g. two `r` ports, or an
  `rw` plus an `r`, all raise a config error).
- `march-2rw` requires **exactly two `rw` ports** — one `rw` plus one `r`
  (or `w`), or only one `rw` port, is rejected.
- Every other algo (`march-c`, `march-raw`, `march-x`, `mats-plus`) still
  requires **exactly one port**, of any type — a 2-port config is rejected
  for them.

A 1R1W config (one read-only port, one write-only port):

```yaml
memory_name: "sram_1r1w_64x32"
wrapper_module_name: "sram_1r1w_64x32_mbist"
addr_width: 6
data_width: 32
we_active_low: true
ports:
  rport:
    type: r
    clk: clkA
    addr: addrA
    dout: doutA
    csb: csbA
  wport:
    type: w
    clk: clkB
    addr: addrB
    din: dinB
    csb: csbB
    we: webB
```

Generate and simulate it exactly like a single-port memory, just adding
`--algo march-1r1w`:

```bash
autombist generate --config config.yml --out out --algo march-1r1w
autombist simulate --out out/sram_1r1w_64x32
```

A 2RW config (both ports fully symmetric `rw`):

```yaml
memory_name: "sram_2rw_64x32"
wrapper_module_name: "sram_2rw_64x32_mbist"
addr_width: 6
data_width: 32
we_active_low: true
ports:
  porta:
    type: rw
    clk: clkA
    addr: addrA
    din: dinA
    dout: doutA
    csb: csbA
    we: webA
  portb:
    type: rw
    clk: clkB
    addr: addrB
    din: dinB
    dout: doutB
    csb: csbB
    we: webB
```

```bash
autombist generate --config config.yml --out out --algo march-2rw
autombist simulate --out out/sram_2rw_64x32
```

**Port-to-pin mapping differs between the two algos.** march-1r1w maps ports
by *type*: the `r` port always drives the algorithm's `sram_*0` pins and the
`w` port always drives `sram_*1`, regardless of the order you write them in
the YAML. march-2rw cannot do this (both ports are the same type, `rw`), so
it maps by **YAML/dict insertion order** instead: the *first* entry in your
`ports:` map is wired to `sram_*0`, and the *second* to `sram_*1`. Reordering
the two entries in a march-2rw config swaps which named port is "port 0" vs
"port 1" — this does not happen with march-1r1w.

Your existing single-port configs (the flat `{clk, addr, din, dout, we,
csb}` form) are completely unaffected — they still work unchanged and render
byte-identical output. The named-port-map shape is additive.

One functional note specific to march-2rw: its `test_mode=0` functional
boundary is inherently single-port — only the port wired to `sram_*0` drives
`func_dout` in functional mode. The second port exists for the MBIST
algorithm's internal concurrency, not for external dual-port functional
access.

### 2b. The `port-coupling` fault type, and its topology restriction

In addition to `stuck-at`, `transition-up`, and `transition-down`,
autoMBIST's classic path supports a fourth fault type: `port-coupling` — an
aggressor write on the write port disturbing a victim read on the read port,
sensitized only by a genuine same-cycle, same-address concurrent access.

**`port-coupling` is restricted to `march-1r1w` only.** It requires exactly
the asymmetric one-read-port/one-write-port topology to have a well-defined
aggressor/victim role split; it is rejected outright for `march-2rw` (whose
two ports are symmetric — there's no fixed "the write port" to be the
aggressor) and for any single-port memory. Passing `--fault-type
port-coupling` with `--algo march-2rw`, or without a 2-port config at all,
fails with a config error rather than silently doing something else.

```bash
autombist generate --config config.yml --out out --test --faults 20 \
  --algo march-1r1w --fault-type port-coupling
autombist simulate --out out/sram_1r1w_64x32
```

march-2rw instead supports `stuck-at`, `transition-up`, and
`transition-down` (the same three fault types single-port memories use):

```bash
autombist generate --config config.yml --out out --test --faults 20 \
  --algo march-2rw --fault-type stuck-at
autombist simulate --out out/sram_2rw_64x32
```

### 2c. march-1r1w is the only multi-port algo with on-chip self-repair

`march-1r1w` is currently the sole multi-port algorithm that supports
on-chip self-repair (`redundancy.onchip_selfrepair: true` — the autonomous
BIRA analyzer + BISR sequencer + row remap, see the
[README's BIRA/BISR section](https://github.com/ranaumarnadeem/autoMBIST/blob/main/README.md#redundancy-repair-birabisr-and-physical-closure)).
Config validation carves out exactly one exception to the "redundancy is
single-port only" rule: the 1R1W `r`+`w` shape is accepted when
`onchip_selfrepair: true` is set, and rejected otherwise. `march-2rw` gets
no such exception — its two ports' concurrent same-cycle compare breaks
the on-chip analyzer's single-fail-per-cycle assumption, so it's not (and
won't become) self-repair-capable without new arbitration RTL.

The mechanism works because both ports share the FSM's single `addr_q`
register (`rtl/march_1r1w/march_1r1w_fsm.sv`): the read port and write
port always access the same address on the same cycle, so one
`fail_valid`/`fail_addr` stream and one `repair_remap_row` instance can
steer both ports together.

```yaml
redundancy:
  num_spare_rows: 1
  num_spare_cols: 0
  onchip_selfrepair: true
```

Add that block to the 1R1W config from §2a and generate with `--algo
march-1r1w` as usual — no other config changes are needed. This has been
hardened through LibreLane against a real, genuinely dual-port sky130
OpenRAM macro (`sky130_sram_1r1w_32b256w`): the flow completes all 80
stages, LVS and Antenna both pass. DRC does not — the dominant violation
traces to a documented, currently-unresolved OpenROAD/sky130 tapcell
limitation, not to anything in this project's RTL or config. See
[`flow/newalgo/README.md`, "march-1r1w: real, genuinely-dual-port macro"](https://github.com/ranaumarnadeem/autoMBIST/blob/main/flow/newalgo/README.md#march-1r1w-real-genuinely-dual-port-macro-hardened-drc-not-clean)
for the full breakdown.

## 3. Algo-shell: 2-port sessions and cross-port faults

The algo-shell (`autombist algo`) is the interactive research shell for the
richer 31-primitive functional fault library (stuck-at, transition,
write/read disturb, address-decoder, and all nine coupling classes: CFIN,
CFID, CFST, CFDS, plus the two-cell family CFTR/CFWD/CFRD/CFIR/CFDRD), run
through Verilator. A 2-port session sees at most 30 of the 31: DRF's
idle-cycle tracking is a single scalar register that has not been extended
to `num_ports = 2`, so `gen_faults --all-types` omits it there. It has its own, independent
multi-port surface, separate from the classic path's `ports:`/`--algo`
config above.

### 3a. Starting a 2-port session

`set_memory` takes a `--ports` flag (default 1):

```
algo> set_memory 8 8 --ports 2
memory set: 8x8, init=1, ports=2
```

`--ports 2` switches the session to the `march_engine_mp.sv` engine (the
2-port sibling of `march_engine.sv`) and renders `fault_ram.sv` with
`num_ports=2` for every subsequent `run`/`compare_algo`. `--ports` must be 1
or 2; anything else is rejected.

### 3b. Cross-port faults: the 9-token `add_fault` syntax

`add_fault` normally takes 3 or 7 tokens:

```
add_fault TYPE VADDR VBIT [AADDR ABIT P0 P1]
```

For a 2-port session, it accepts two more trailing tokens — 9 total — to
select which physical port the victim and aggressor access are on:

```
add_fault TYPE VADDR VBIT AADDR ABIT P0 P1 VPORT APORT
```

`VPORT`/`APORT` default to 0 when omitted, so every pre-existing 7-token
fault definition keeps its original (same-port) meaning under a 2-port
session too.

**`APORT` is the load-bearing one.** It gates the aggressor match, and is
honoured by three of the four *aggressor-driven* coupling primitives — CFIN
and CFID (in the write-aggressor loop) and CFDS (in both loops). **CFST does
not honour it**: CFST is a static clamp, so its arm is emitted into
`clamp_static()`, which re-asserts stored state on every access and has no
`port` in scope.

**The two-cell family (CFTR/CFWD/CFRD/CFIR/CFDRD) does not honour it
either**, for the same underlying reason as CFST rather than by oversight:
`APORT` selects which port performs the *aggressor access*, and these types
have no aggressor access. They are victim-operation faults gated on the
aggressor's stored **state** (`sensitize.agg_pre`), read as
`mem[aa][ab]` with no port in scope. Setting `APORT` on one is silently
inert. Every non-coupling primitive is port-agnostic by construction.

**`VPORT` is parsed but not yet honoured.** No expression in the generated
engine reads it — the victim-side guards match on address and bit alone — so
setting it currently changes nothing for any fault type. It is reserved for a
future per-port victim gate. Scope a fault to a port with `APORT`.

- **Same-port coupling** (`APORT` omitted/0): the aggressor's sensitizing op is
  evaluated against port 0 — the ordinary, pre-multi-port behavior.
- **Cross-port coupling** (`APORT` naming the other port): the aggressor's
  sensitizing op is issued on a *different* physical port than the one used to
  observe the victim — e.g. a write on port 1 disturbing a cell later read back
  via port 0. This is the case that only a genuine dual-port fault model can
  express.

Example — a cross-port CFIN (aggressor on port 1, victim sensed on port 0):

```
algo> add_fault CFIN 5 1 6 1 2 0 0 1
fault added: CFIN v=5.1 ports=0/1 (total 1)
```

The first `0` after `2` is `P1` (unused by CFIN's semantics, must still be
present as a placeholder), then `0 1` is `VPORT APORT` — victim sensed on
port 0, aggressor issued on port 1.

A fault list file uses the same 9-field-per-line format (`TYPE VADDR VBIT
AADDR ABIT P0 P1 VPORT APORT`); the trailing `VPORT APORT` pair is optional
there too, so any pre-multi-port 7-field fault-list file still parses
unchanged. `.alg` files gain a parallel per-op `.PORT` suffix (e.g. `w1.0`,
meaning "issue this write on port 0") for driving which port the algorithm
itself uses when it's running against a 2-port engine — see
`src/autombist/engine/README.md`'s "Multi-port" section for the full
grammar and worked differential-proof example (same-port detected,
cross-port detected, and an escape control where the fault list claims
cross-port but the algorithm never actually issues on that port).

Everything else about the algo-shell workflow — `run`, `compare_algo`,
`write_report`, `write_diagnosis`, `export_tb` — works the same way against
a 2-port session as a single-port one; only `set_memory --ports` and
`add_fault`'s trailing tokens change.

## 4. FSM-under-test multi-port (a separate, distinct surface)

`add_fsm` in the algo-shell lets a researcher validate their **own**
MBIST controller FSM — not one of autoMBIST's built-in march engines —
against the same fault-campaign machinery. This has its own multi-port
contract, `REQUIRED_PORTS_MP`, and it is a **different surface** from both
of the above:

- It's not the classic path's `ports:` config block (that describes your
  *memory's* pins so autoMBIST can wrap it; `add_fsm` instead validates
  *your controller's* pins).
- It's not the algo-shell's built-in `set_memory --ports 2` + march-1r1w/
  march-2rw campaign (those exercise autoMBIST's own algorithms; `add_fsm`
  exercises a black-box FSM you supply).

When the session's memory was configured with `set_memory --ports 2`,
`add_fsm` validates the registered FSM against the 2-port port contract
(`REQUIRED_PORTS_MP`): both a `sram_*0` bus and a full `sram_*1` bus
(`sram_clk1`, `sram_csb1`, `sram_web1`, `sram_addr1`, `sram_din1`,
`sram_dout1`), on top of the shared `clk`/`rst_n`/`bist_start`/`bist_done`/
`bist_fail` handshake — mirroring `rtl/march_2rw/march_2rw_top.sv`'s
pinout. With a single-port session (the default), the single-port contract
(`REQUIRED_PORTS`, just the `sram_*0` bus) applies instead. Registering an
FSM whose ports don't match the active contract raises a structural
port-diff error naming exactly which ports are missing or mis-directioned.

Use this when you have a hand-written or third-party MBIST controller you
want to grade against autoMBIST's fault library — as opposed to using
autoMBIST's own march-1r1w/march-2rw algorithms (sections 2 and 3 above),
which is what most users testing "a 2-port memory" actually want.

```
algo> set_memory 8 8 --ports 2
algo> add_fsm my_2port_controller_top.sv
algo> run my_2port_controller_top
```

FSM runs report detect/escape only — a black-box controller's `bist_fail`
is a single bit, not an algorithm's internal step counter, so there's no
per-element/per-op attribution the way there is for `run <algo_name>`.

## 5. The shared-array architectural note

Both the classic path and the algo-shell's multi-port support rest on one
non-negotiable invariant: a 2-port memory model must share exactly **one**
underlying storage/fault-record array across both ports. Two independently
instantiated single-port cores wired side by side will build and simulate
without error, but they silently defeat the entire point of dual-port
testing — cross-port coupling faults are only observable if both ports'
accesses resolve against the *same* underlying state.

This guide doesn't re-derive that invariant or show the template internals
that enforce it — see **[architecture.md, "The multi-port invariant: one
shared core, not two"](architecture.md#the-multi-port-invariant-one-shared-core-not-two)**
for the full explanation (with the relevant `fault_ram_template.sv.j2` /
`openram_shim_mp.sv` excerpts) of how both subsystems satisfy it. If you're
ever plugging in your own dual-port memory model rather than using
autoMBIST's generated one, that invariant is the one thing to verify your
model actually preserves.
