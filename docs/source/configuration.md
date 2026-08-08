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

Unknown top-level keys are rejected (with a suggestion when one is close to a
real key) rather than silently ignored — a typo like `adr_width` used to sit
there unread while `addr_width` quietly kept its default.

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

## `memory_has_fixed_geometry` — real macros keep their own widths

```yaml
memory_has_fixed_geometry: true   # optional, default false
```

By default the wrapper instantiates the memory with
`#(.ADDR_WIDTH(...), .DATA_WIDTH(...))` taken from the config. That is right for
this project's behavioral models, which size their internal array from those
parameters. It is **wrong for a real compiled macro**, whose widths are baked in
at compile time and cannot be resized.

Concretely: `sky130_sram_8b1024w` is declared as 1024 words of 8 bits, but its
model has `DATA_WIDTH = 9` (a spare column) and `ADDR_WIDTH = 11` (spare rows).
Passing `10`/`8` resizes it to a plain 1024x8 array — the spares disappear, and
you are no longer simulating the macro you would fabricate.

Set `memory_has_fixed_geometry: true` and the memory is instantiated without a
parameter block, keeping its real geometry. The wrapper's narrower nets meet the
macro's wider ports the way real hardware would: the address zero-extends to the
logical words, `din` zero-extends (the spare column takes 0, and `spare_wen` is
tied off unless column repair drives it), and `dout` truncates the spare back
off.

This affects only the memory. The MBIST controller is this project's own RTL and
stays parameterized either way.

Note the practical scope: for plain MBIST both settings behave identically,
because the march only ever exercises the logical array. The flag matters when
you want the simulated model to *be* the compiled macro — including its spare
rows and spare column — rather than a resized stand-in.

## Redundancy repair (BIRA/BISR)

Opt-in via a `redundancy:` block, paired with either `repair_ports:`
(tester-driven) or `onchip_selfrepair: true` (autonomous). Single-port
memories only, except the 1-read+1-write `march-1r1w` port shape when paired
with `onchip_selfrepair: true` (see below). `num_spare_cols` may be non-zero
on the **tester-driven** path (see "Column repair" below); it must be `0` with
`onchip_selfrepair: true`, whose analyzer is row-only.

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

`row_repair_en` and `faulty_row_addr` are required by name on this path, with
the exact widths shown above (`num_spare_rows` and `num_spare_rows *
addr_width`) — a missing or mis-sized entry is rejected at config load rather
than generating a wrapper whose repair pin is left unconnected.

**Autonomous on-chip self-repair** — no `repair_ports:`; the wrapper gains
`self_repair_start` / `self_repair_done` / `self_repair_fail` /
`self_repair_busy` instead, and repairs itself from a single start pulse:

```yaml
redundancy:
  num_spare_rows: 1
  num_spare_cols: 0
  onchip_selfrepair: true
```

`onchip_selfrepair` currently requires `--algo` to be one of `march-c`,
`march-raw`, `march-1r1w` (a 1-read-port + 1-write-port config, `type: r`/
`type: w`), `march-x`, or `mats-plus`. `march-2rw` isn't wired up yet -- its
two concurrent same-cycle compares break the on-chip analyzer's
single-fail-per-cycle assumption and would need new arbiter RTL.

`march-1r1w` is the one multi-port shape `redundancy:` accepts, and only in
this autonomous form (no `repair_ports:` — self-repair and tester-driven
repair are mutually exclusive). A single `repair_remap_row` steers both
ports, since `march_1r1w_fsm` drives them off the same shared address
register:

```yaml
ports:
  rport:
    type: r
    clk: clk0
    addr: addr0
    dout: dout0
    csb: csb0
  wport:
    type: w
    clk: clk1
    addr: addr1
    din: din1
    csb: csb1
    we: web1
redundancy:
  num_spare_rows: 2
  num_spare_cols: 0
  onchip_selfrepair: true
```

### Column repair

Set `num_spare_cols` to a non-zero value on the **tester-driven** path and the
wrapper gains a second external remap, `repair_remap_col`, on the *data* path:
it copies each faulty bit lane onto a spare column on writes (driving the
memory's `spare_wen` pin) and substitutes the spare back in on reads. The row
and column remaps are independent — one steers addresses, the other steers
bits — so they compose: a row-steered access still gets its column steer, on
the spare row's own spare columns.

This requires three additional things:

* the memory's spare-column write-enable pin, declared as a `spare_wen` port
  role (e.g. `spare_wen0` on an OpenRAM macro);
* two extra `repair_ports` with canonical names and exact widths —
  `col_repair_en` (`num_spare_cols` bits) and `faulty_bit`
  (`num_spare_cols * ceil(log2(data_width))` bits);
* `words_per_row: 1` (the default). Column muxing is rejected: OpenRAM shares
  one spare-column set per *physical* row across the muxed words, which the
  repair model's global bit-lane view does not express.

```yaml
ports:
  clk: clk0
  addr: addr0
  din: din0
  dout: dout0
  we: web0
  csb: csb0
  spare_wen: spare_wen0        # required when num_spare_cols > 0
redundancy:
  num_spare_rows: 1
  num_spare_cols: 1
```

### Declaring `spare_wen` without column repair

`spare_wen` is required when `num_spare_cols > 0`, but it is **allowed, and
recommended, whenever the memory physically has the pin** — even with no column
repair, and even with no `redundancy:` block at all. The role states a fact
about the macro, not an intent to repair with it.

Declare it and the wrapper ties the pin off (`'0`, spare-column writes
disabled). Omit it and the wrapper cannot connect the pin at all, leaving a real
macro's input **floating** — an OpenRAM macro compiled with spare columns has
`spare_wen0` whether or not your design uses it.

```yaml
ports:
  clk: clk0
  addr: addr0
  din: din0
  dout: dout0
  we: web0
  csb: csb0
  spare_wen: spare_wen0        # tied off -- no redundancy block at all
```

The same applies with row repair but no column repair: declare `spare_wen`
alongside `num_spare_rows` and it is tied off rather than left dangling.

Declaring the column *repair pins* (`col_repair_en`/`faulty_bit`) without
`num_spare_cols` is still an error — unlike `spare_wen`, those exist only to
carry a repair value, so declaring them with nowhere for it to go is
unambiguously a mistake.

`repair.analyze()` has always been a 2D solver; `repair.encode_repair()` packs
both halves of its verdict into these pins. (`repair.encode_row_repair()` is
the row-only encoder and now refuses a geometry with spare columns rather than
silently emitting half a repair.)

One behavioural caveat worth knowing: a spare column is only *written* while
`col_repair_en` is high, so a spare lane holds stale data until the first write
to that address after the signature is applied. Every flow here writes before
it reads, so this is bounded in practice — but enabling repair midway through
and reading without rewriting will surface the stale lane.

## OpenRAM synthesis config (`openram.yml`)

A separate config, consumed by `autombist ram-synth`, describing the OpenRAM
macro to compile (word size, word count, port count, spare rows/columns,
target technology). `autombist init` scaffolds a starter one alongside
`config.yml`.

## Full reference

Every CLI flag that reads or overrides these files — `generate`, `run`,
`ram-synth`, `harden` — is documented flag-by-flag in
{doc}`cli-reference`, including the full `reports/latest.json` output
schema.
