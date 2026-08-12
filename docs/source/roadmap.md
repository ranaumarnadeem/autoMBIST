# Roadmap

Where things stand, grouped roughly by how far off they are. Nothing here is
a promise or a deadline — just an honest picture of what's built, what's in
progress, and what's further out.

## Done

- MBIST wrapper generation for single- and multi-port memories (`march-c`,
  `march-raw`, `march-1r1w`, `march-2rw`, `march-x`, `mats-plus`)
- A 29-primitive functional fault model and research shell, independent of
  any real memory macro, with seven built-in march algorithms (`march_b`,
  `march_c`, `march_c_plus`, `march_ss`, `march_x`, `march_y`, `mats_plus`) —
  a separate list from the classic-path wrapper-generation algorithms above
- BIRA (redundancy analysis) as a 2D solver, both row and column allocation
- BISR — tester-driven, and (for every algo except `march-2rw`: `march-c`,
  `march-raw`, `march-x`, `mats-plus`, and the multi-port `march-1r1w`) a
  fully autonomous on-chip self-repair FSM
- Column repair on the tester-driven path — an external `repair_remap_col`
  bit-steer mux driving a memory's `spare_wen`, composing with the row remap
- Repair persistence across a reset, at the register level: a saved signature
  can be reloaded into the on-chip analyzer before any access
  (`onchip_repair_persistence: true`)
- A proven LibreLane hardening recipe for real OpenRAM sky130 macros,
  including self-repair-wrapped variants across multiple algorithms
  (march-c, march-x, mats-plus) — this is the top-level place-and-route
  closure, which treats each macro as opaque hard IP; per-macro DRC/LVS
  signoff for the macros' own GDS is separately tracked below
- An SoC-level demonstration: an unmodified RV32I core (PicoRV32) booting and
  running a real program through self-repaired memory, both against
  defect-injectable behavioral models and the hardened OpenRAM macros (same
  per-macro signoff caveat as above)

## In progress

- A real Liberty timing view for hardened macros (today's runs black-box
  memory timing rather than using measured numbers)
- A merged full-hierarchy DRC pass that checks macro polygons directly
  instead of treating the macro as an opaque boundary
- Macro-level DRC/LVS signoff for the demo macros' own GDS — currently
  blocked on updating the vendored `OpenRAM/` checkout past two upstream
  fixes for a wordline-pin-numbering defect in the sky130 replica bitcell
  array; see {doc}`challenges` for the root cause
- Extending on-chip self-repair to `march-2rw` (needs new arbiter RTL: its two
  concurrent same-cycle compares break the analyzer's single-fail-per-cycle
  assumption, unlike `march-1r1w`'s single shared compare)

## Further out

- Column repair on the *autonomous on-chip* path (today it is tester-driven
  only — the on-chip analyzer is row-only, and a 2D one needs both a per-bit
  fail dimension in the controller RTL and an on-chip heuristic analyzer)
- Real fuse/NVM device physics behind repair persistence (today's persistence
  is register-level: the load path exists, the storage element is out of scope)
- A broader march-algorithm library (checkerboard, galloping, and similar
  patterns beyond the current built-ins)
- A standard test-access wrapper (IEEE 1500/1687-style) for integrating
  autoMBIST into a larger SoC test network
- A shared controller across multiple memories, rather than one controller
  instance per memory

## How to help

If any of this overlaps with something you're working on, or you'd like to
pick up an item, open an issue — see
[CONTRIBUTING.md](https://github.com/ranaumarnadeem/autoMBIST/blob/main/CONTRIBUTING.md).
