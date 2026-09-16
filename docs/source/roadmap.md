# Roadmap

Where things stand, grouped roughly by how far off they are. Nothing here is
a promise or a deadline — just an honest picture of what's built, what's in
progress, and what's further out.

## Done

- MBIST wrapper generation for single- and multi-port memories (`march-c`,
  `march-raw`, `march-1r1w`, `march-2rw`, `march-x`, `mats-plus`)
- A 31-primitive functional fault model and research shell, independent of
  any real memory macro, with seven built-in march algorithms (`march_b`,
  `march_c`, `march_c_plus`, `march_ss`, `march_x`, `march_y`, `mats_plus`) —
  a separate list from the classic-path wrapper-generation algorithms above
- BIRA (redundancy analysis) as a 2D solver, both row and column allocation
- BISR — tester-driven, and (for every current algo: `march-c`, `march-raw`,
  `march-x`, `mats-plus`, and the multi-port `march-1r1w`/`march-2rw`) a fully
  autonomous on-chip self-repair FSM. march-2rw's concurrent same-cycle dual
  compare turned out not to need arbiter RTL — its algorithm table only ever
  compares both ports against the same address, verified directly against the
  table and hardened as a regression assertion, not just assumed — and its
  addition also generalized the wrapper's repair remap to one instance per
  port (previously a single shared instance that only happened to be correct
  for march-1r1w's own address-sharing structure)
- Column repair on the tester-driven path — an external `repair_remap_col`
  bit-steer mux driving a memory's `spare_wen`, composing with the row remap
- Column repair on the *autonomous on-chip* path (`onchip_col_repair: true`),
  now for every self-repair-capable algo: `march-c`/`march-raw`/`march-x`/
  `mats-plus`, and both multi-port algos, `march-1r1w` and `march-2rw`. A
  per-bit `fail_bitmask` stream from the FSM plus a new on-chip 2D heuristic
  analyzer (`onchip_2d_repair_analyzer`). Not a hardware implementation of
  BIRA's exact backtracking search — a disclosed, single-pass approximation
  that can report a repairable chip unrepairable in some cases (never a false
  pass, since verify-by-re-execution is independent of the analyzer's own
  bookkeeping), proven against a hand-constructed counterexample checked
  directly against `bira.py`, not just asserted. Neither multi-port addition
  was a mechanical repeat of the single-port case, and the two needed
  genuinely different wrapper designs from each other: the multi-port branch
  had never carried a `repair_remap_col` instance before (there is no
  tester-driven multi-port path to have built one for). `march-1r1w`'s clean
  read-only/write-only port split lets ONE instance serve the whole design,
  cross-wired across the write port's `din`/`spare_wen` and the read port's
  `dout`. `march-2rw`'s two ports are both fully read/write and can write
  DIFFERENT addresses the same cycle, so neither is exclusively "the" reader
  or writer — it needs TWO independent instances, one per port, each with its
  own `spare_wen` (`repair_remap_col` has no address input, so the two
  compose safely with no shared state to race on). A related, non-obvious
  property worth recording: `march-2rw`'s own algorithm structure (both ports
  always write the identical value when writing concurrently; both always
  read the identical address, from the identical physical storage cell, when
  reading concurrently) means a wrapper bug that swapped which port's wiring
  fed which instance would be invisible to *any* simulation — closed instead
  by render-text assertions checking the exact generated wire names
- Repair persistence across a reset, at the register level: a saved signature
  can be reloaded into the on-chip analyzer before any access
  (`onchip_repair_persistence: true`)
- On-chip diagnosis logging (`onchip_diagnosis: true`): a full-range
  fail-address accumulator (`onchip_diagnosis_log`) that captures every
  distinct failing row from the most recent self-repair analyze pass,
  independent of (and typically sized larger than) the physical spare budget
  `onchip_row_repair_analyzer` is bounded to — verified end-to-end that it
  sees defects the repair analyzer itself can't fit. Usable via direct
  `diag_valid`/`diag_addr`/`diag_overflow` pins today; JTAG/IJTAG wrapping is
  still pending (see Further out)
- A march-test synthesizer that constructs a test directly from the fault
  model rather than only grading a hand-written one — 27n at 16 elements,
  verified 38/38 against real Verilator at both memory init values
- A `checkerboard` built-in for the research shell (logical address-LSB
  parity, not physical row/column adjacency — no consumer in this toolkit
  has physical geometry). Needed a genuinely new capability, not just a new
  `.alg` file: every existing op's value was a fixed function of phase alone,
  so the DSL gained four address-DEPENDENT ops (`wc`/`wcb`/`rc`/`rcb`,
  negative op codes to avoid the wait-op space) threaded through both march
  engines. Scores 20/29 on `faults.example.txt` — the same total as march_c,
  but a different profile (catches SOF, which march_c misses; misses
  CFin/CFid, which march_c catches). Research-shell only for now — the
  separate RTL wrapper-generation path (real synthesizable BIST hardware)
  doesn't have it yet, see below
- `wrap-test-access`: wraps a generated design's control/status ports with an
  IEEE 1149.1/1687 (JTAG/IJTAG) test-access network and can emit its ICL
  description, via the external [warptap](https://github.com/ranaumarnadeem/warptap)
  package — verified with a real Icarus simulation of the inserted RTL,
  reading `self_repair_busy` through the scan path after writing
  `self_repair_start`, against the real three-macro `mem_subsystem_mbist`.
  Also wraps `diag_overflow` (`--onchip-diagnosis`, confirmed single-bit) —
  the rest of diagnosis readback and the wide repair ports remain further out
  (see below)
- A proven LibreLane hardening recipe for real OpenRAM sky130 macros,
  including self-repair-wrapped variants across multiple algorithms
  (march-c, march-x, mats-plus) — this is the top-level place-and-route
  closure, which treats each macro as opaque hard IP; per-macro DRC/LVS
  signoff for the macros' own GDS is separately tracked below
- An SoC-level demonstration: an unmodified RV32I core (PicoRV32) booting and
  running a real program through self-repaired memory, both against
  defect-injectable behavioral models and the hardened OpenRAM macros (same
  per-macro signoff caveat as above)

## Further out

- Real fuse/NVM device physics behind repair persistence (today's persistence
  is register-level: the load path exists, the storage element is out of scope)
- A broader march-algorithm library: `checkerboard` is done (see above) --
  galloping/GALPAT and similar patterns beyond the current built-ins remain
  open. Also open: `checkerboard` on the RTL wrapper-generation path (a new
  `rtl/checkerboard/` algo+fsm+top triple with an address-dependent write-data
  mux, not just a `.alg` file) for real synthesizable BIST hardware, not just
  the research-shell fault-coverage proof
- Test-access wrapping for the ports this doesn't cover yet: the rest of
  diagnosis readback (`diag_valid`/`diag_addr` — real boundary ports, now
  available via direct pins, see on-chip diagnosis logging above; `diag_overflow`
  itself is already wrapped, see `wrap-test-access` in the Done section) and
  the wide repair ports (`fuse_*`, `row_repair_en`, `col_repair_en`,
  `faulty_bit`) — both multi-bit, blocked on an upstream `icl_parser` bug in
  warptap's vendored ICL parsing (not on any RTL gap); a fix is in and a new
  warptap release is expected, at which point this is worth revisiting
- A shared controller across multiple memories, rather than one controller
  instance per memory

## How to help

If any of this overlaps with something you're working on, or you'd like to
pick up an item, open an issue — see
[CONTRIBUTING.md](https://github.com/ranaumarnadeem/autoMBIST/blob/main/CONTRIBUTING.md).
