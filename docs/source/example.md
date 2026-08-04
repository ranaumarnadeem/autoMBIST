# Example: a self-repairing memory subsystem

This walks through the most complete example in the repository:
[`flow/multimem/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/multimem)
— three differently-sized OpenRAM sky130 SRAM macros, each wrapped in an
autonomous MBIST + self-repair block, hardened together as one subsystem.

## The memories

| Macro | Size | Shape |
|---|---|---|
| `sky130_sram_32b256w` | 1 KB | 32-bit × 256 words |
| `sky130_sram_32b512w` | 2 KB | 32-bit × 512 words |
| `sky130_sram_8b1024w` | 1 KB | 8-bit × 1024 words (deep/narrow) |

Each macro is generated with one spare row + one spare column via OpenRAM
(`scripts/synthesize_sram.py`), giving every memory a real, addressable spare
to repair into — not a behavioral stand-in. This example uses the *autonomous
on-chip* self-repair path, which is row-only, so its spare column is present in
silicon but unused here; column repair is tester-driven (see
{doc}`configuration`).

## Generate a self-repairing wrapper

```yaml
# config.yml
memory_name: "sky130_sram_32b256w"
wrapper_module_name: "sram_wrap_a_mbist"
addr_width: 8
data_width: 32
we_active_low: true
ports:
  clk: clk0
  addr: addr0
  din: din0
  dout: dout0
  we: web0
  csb: csb0
redundancy:
  num_spare_rows: 1
  num_spare_cols: 0
  onchip_selfrepair: true
```

```bash
autombist generate --config config.yml --out out --algo march-c
```

:::{note}
**Two build views, one config.** This config is used two ways. For
*simulation* (below), a defect-injectable behavioral model of the macro is
swapped in — that's what lets a real defect be injected to repair against —
and the generator's default `read_latency: 1` matches its timing. For
*hardening* (further below), the real OpenRAM macro is used instead. If you
simulate self-repair directly against that **real** macro model (rather than
the behavioral one), generate with `read_latency: 0`: the real macro's `dout`
timing differs, and the default would make the controller sample a cycle too
late. See {doc}`configuration` for the full `read_latency` guidance.
:::

This produces a wrapper that instantiates:

- the march-C MBIST controller (`march_c_top.sv`),
- `onchip_row_repair_analyzer.sv` — a CAM-style registrar that tracks failing
  rows streamed live from the controller,
- `onchip_selfrepair_ctrl.sv` — an 8-state sequencer that runs the algorithm
  twice (analyze, then independently re-verify) and drives the repair remap,
- `repair_remap_row.sv` — the external address-steering logic itself.

Three of these (one per macro) plus a small bus-arbitration top make up
`mem_subsystem_mbist.sv`.

## Simulate it

```bash
autombist simulate --out out/sky130_sram_32b256w
```

cocotb drives `self_repair_start`, waits for `self_repair_done`, and confirms
the memory passes a post-repair BIST pass — with a defect injected via the
behavioral model's compile-time knob, proving the repair actually happened
(not just that the flag came back "done").

## Harden it to GDS

```bash
autombist fix-lef-units macros_1000/sky130_sram_32b256w.lef   # OpenRAM emits 2000 dbu; sky130A expects 1000
autombist harden --config flow/multimem/mbist/harden.yml --run
```

Result, as of this writing: **DRT 0 violations, LVS-clean including power**,
die 0.91 mm², 7,189 standard cells (the self-repair logic across all three
memories) plus the three macros at their fixed area. Full recipe and the
gotchas it took to get there: {doc}`librelane`.

## Under a real CPU

The subsystem above proves self-repair on a memory bus driven by a testbench.
A companion example
([`flow/soc/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/soc))
goes one step further: an unmodified RV32I core (PicoRV32) boots and runs a
real program through the same self-repair-wrapped memories. Repair completes at
power-on — gated so the CPU is held in reset until every memory reports done —
and then the processor's own `lw`/`sw` traffic round-trips correctly through
the repaired path. It runs both against defect-injectable behavioral memories
(proving repair genuinely fixes a defect the CPU would otherwise read wrong)
and against the real hardened OpenRAM macros
([`flow/soc/hardened/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/soc/hardened)).
That directory's `soc_top_hw` — the CPU plus both self-repair wrappers plus
the real macros — is itself hardened clean through the same LibreLane recipe
as the plain subsystem above, not just simulated against real macros: routing
and LVS close with no CPU-specific issues.

## Where to go from here

- Change `onchip_selfrepair` to a `repair_ports:` block instead, for a
  tester-driven repair flow rather than autonomous.
- Swap in your own OpenRAM-compiled macro — same config shape.
- Grade the underlying march algorithm against the full fault model first,
  with no macro at all: `autombist test --algo march_c --faults faults.txt`.
- march-C isn't the only self-repair-capable algorithm: `onchip_selfrepair`
  also works with `march-raw`, `march-x`, `mats-plus`, and (as a multi-port
  scaffold) `march-1r1w` — only `march-2rw`'s concurrent same-cycle dual
  compare is out of scope by design. See
  [`flow/newalgo/`](https://github.com/ranaumarnadeem/autoMBIST/tree/main/flow/newalgo)
  for march-X and MATS+ each wrapping a different real OpenRAM macro and
  hardened clean through the same LibreLane recipe.
