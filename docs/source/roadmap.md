# Roadmap

Where things stand, grouped roughly by how far off they are. Nothing here is
a promise or a deadline — just an honest picture of what's built, what's in
progress, and what's further out.

## Done

- MBIST wrapper generation for single- and multi-port memories (`march-c`,
  `march-raw`, `march-1r1w`, `march-2rw`, `march-x`, `mats-plus`)
- A 19-primitive functional fault model and research shell, independent of
  any real memory macro
- BIRA (redundancy analysis) as a 2D solver, both row and column allocation
- BISR — tester-driven, and (for every algo except `march-2rw`: `march-c`,
  `march-raw`, `march-x`, `mats-plus`, and the multi-port `march-1r1w`) a
  fully autonomous on-chip self-repair FSM
- A proven LibreLane hardening recipe for real OpenRAM sky130 macros,
  including self-repair-wrapped variants across multiple algorithms
  (march-c, march-x, mats-plus)
- An SoC-level demonstration: an unmodified RV32I core (PicoRV32) booting and
  running a real program through self-repaired memory, both against
  defect-injectable behavioral models and the real hardened OpenRAM macros

## In progress

- A real Liberty timing view for hardened macros (today's runs black-box
  memory timing rather than using measured numbers)
- A merged full-hierarchy DRC pass that checks macro polygons directly
  instead of treating the macro as an opaque boundary
- Extending on-chip self-repair to `march-2rw` (needs new arbiter RTL: its two
  concurrent same-cycle compares break the analyzer's single-fail-per-cycle
  assumption, unlike `march-1r1w`'s single shared compare)

## Further out

- Column repair (today's redundancy support is row-only)
- Repair persistence across a reset (today, repair state is volatile and a
  fresh self-repair pass is needed after every reset)
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
