# Configuration file

The classic path (`generate` / `simulate` / `run`) is driven by one YAML file,
conventionally `config.yml`. `autombist init` scaffolds a starter one.

## Top-level fields

```yaml
memory_name: "sram_1rw"            # the memory module's Verilog name
wrapper_module_name: "sram_1rw_mbist"
addr_width: 10
data_width: 32
we_active_low: true
ports: { ... }                      # see below
```

## Single-port memories

```yaml
ports:
  clk: clk0
  addr: addr0
  din: din0
  dout: dout0
  we: web0
  csb: csb0
```

This flat form is the legacy shape and still renders byte-identical output —
no existing config needs to change if you started here.

## Multi-port memories

A named `ports:` map switches on multi-port generation. Two shapes are
supported, selected by `--algo` at generate time:

**`march-1r1w`** — one read-only port, one write-only port (`type: r` /
`type: w`):

```yaml
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

**`march-2rw`** — two fully symmetric read/write ports (`type: rw` on both).
The **first entry** in the map (YAML insertion order) always wires to the
algorithm's `sram_*0` pins, the second to `sram_*1` — swapping the two
entries' order in the file swaps that mapping:

```yaml
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

## Read latency

```yaml
read_latency: 1   # optional, default 1
```

`read_latency` tells the generated MBIST controller how many clock cycles to
wait, after issuing a read, before sampling the memory's `dout`. **It must
match the read timing of your specific memory model, or the controller samples
`dout` at the wrong moment and reports failures on a perfectly good memory.**

- **`read_latency: 1`** (default) suits a memory that registers the address on
  one clock edge and presents `dout` on the next, holding it stable — this
  project's behavioral fixtures (`rtl/sram_model_spares.sv` and friends) work
  this way, which is why 1 is the default.
- **`read_latency: 0`** suits a memory whose `dout` is valid the same cycle the
  address is presented and then decays (e.g. an X, or a hold value) shortly
  after — **this is how OpenRAM's own behavioral macro models behave** (`dout0`
  is driven on the clock's negedge and forced back to `x` shortly after the
  next posedge). Generating self-repair wrappers around a **real OpenRAM
  macro** therefore needs `read_latency: 0`; leaving it at the default makes
  the on-chip self-repair engine sample a cycle too late and declare a
  defect-free macro unrepairable.

If you see MBIST or self-repair reporting failures you don't believe are real —
especially a "phantom" unrepairable on a known-good memory — a mismatched
`read_latency` is the first thing to check. `flow/multimem/mbist/` (the real
sky130 macro subsystem) sets `read_latency: 0` for exactly this reason.

## Redundancy repair (BIRA/BISR)

Opt-in via a `redundancy:` block, paired with either `repair_ports:`
(tester-driven) or `onchip_selfrepair: true` (autonomous). Single-port memories
only; `num_spare_cols` must currently be `0` (row repair only — see
{doc}`roadmap`).

**Tester-driven** — the wrapper exposes repair-register pins a tester (or the
Python `repair/bisr.py` encoder) drives directly:

```yaml
redundancy:
  num_spare_rows: 2
  num_spare_cols: 0
repair_ports:
  - {name: row_repair_en, width: 2, dir: input}
  - {name: faulty_row_addr, width: 20, dir: input}   # num_spare_rows * addr_width
```

**Autonomous on-chip self-repair** — no `repair_ports:`; the wrapper gains
`self_repair_start` / `self_repair_done` / `self_repair_fail` /
`self_repair_busy` instead, and repairs itself from a single start pulse:

```yaml
redundancy:
  num_spare_rows: 1
  num_spare_cols: 0
  onchip_selfrepair: true
```

`onchip_selfrepair` is currently gated to `--algo march-c` (the on-chip
analyzer streams fail data only from that FSM today).

## OpenRAM synthesis config (`openram.yml`)

A separate config, consumed by `autombist ram-synth`, describing the OpenRAM
macro to compile (word size, word count, port count, spare rows/columns,
target technology). `autombist init` scaffolds a starter one alongside
`config.yml`.

## Full reference

Every CLI flag that reads or overrides these files — `generate`, `run`,
`ram-synth`, `harden` — is documented flag-by-flag in
[`docs/cli-reference.md`](https://github.com/ranaumarnadeem/autoMBIST/blob/main/docs/cli-reference.md),
including the full `reports/latest.json` output schema.
