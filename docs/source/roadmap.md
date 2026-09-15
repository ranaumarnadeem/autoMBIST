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
- `wrap-test-access`: wraps a generated design's control/status ports with an
  IEEE 1149.1/1687 (JTAG/IJTAG) test-access network and can emit its ICL
  description, via the external [warptap](https://github.com/ranaumarnadeem/warptap)
  package — verified with a real Icarus simulation of the inserted RTL,
  reading `self_repair_busy` through the scan path after writing
  `self_repair_start`, against the real three-macro `mem_subsystem_mbist`
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

- Column repair on the *autonomous on-chip* path (today it is tester-driven
  only — the on-chip analyzer is row-only, and a 2D one needs both a per-bit
  fail dimension in the controller RTL and an on-chip heuristic analyzer)
- Real fuse/NVM device physics behind repair persistence (today's persistence
  is register-level: the load path exists, the storage element is out of scope)
- A broader march-algorithm library (checkerboard, galloping, and similar
  patterns beyond the current built-ins)
- Test-access wrapping for the ports this doesn't cover yet: diagnosis
  readback (`diag_valid`/`diag_addr`/`diag_overflow`, now available via
  direct pins — see on-chip diagnosis logging above; wrapping itself is
  blocked on an upstream `icl_parser` bug in warptap's vendored ICL parsing,
  tracked separately, not on any RTL gap) and the wide repair ports
  (`fuse_*`, `row_repair_en`, `col_repair_en`, `faulty_bit`) — see
  `wrap-test-access` in the Done section above for what's already covered
- A shared controller across multiple memories, rather than one controller
  instance per memory

## How to help

If any of this overlaps with something you're working on, or you'd like to
pick up an item, open an issue — see
[CONTRIBUTING.md](https://github.com/ranaumarnadeem/autoMBIST/blob/main/CONTRIBUTING.md).
