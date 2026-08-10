# Flow

autoMBIST ships **two independent subsystems** behind one CLI. They share no
runtime state — understanding why they're separate is the fastest way to
understand the codebase.

| | Classic path | Algo-shell |
|---|---|---|
| Entry points | `generate` / `simulate` / `run` | `test` / `algo` |
| Simulator | Icarus Verilog (cocotb) | Verilator 5.x |
| Subject under test | A real memory macro, wrapped | A behavioral fault-injectable RAM model — no real macro |
| Fault model | Structural array faults (stuck-at, transition, port-coupling) | 21 functional fault primitives |
| Answers | "Does the generated wrapper catch faults on *this* memory, synthesizably?" | "How good is this march algorithm, against a known fault model?" |

## Classic path — RTL wrapping

```{mermaid}
flowchart LR
    A[config.yml] --> B[generator.py]
    B --> C[wrapper_template.j2]
    C --> D["mem_mbist.v"]
    B -.-> E[saboteur_template.j2]
    E -.-> F["mem_saboteur.v --test"]
    D --> G["cocotb + Icarus / runner.py"]
    F -.-> G
    G --> H["reporting.py"]
    H --> I["results.json / report.txt"]
```

`generator.py` validates the config, normalizes whatever `ports:` shape you
wrote into a canonical form, and renders a wrapper that muxes between
functional access and MBIST access. With `--test`, it additionally renders a
saboteur — a fault-injecting stand-in for the memory — for verification. This
is the path that produces the RTL you'd actually synthesize and tape out.

## Algo-shell — fault-model research platform

```{mermaid}
flowchart LR
    A[".alg file"] --> C[march_engine.sv]
    B[fault list] --> C
    D[fault_primitives.py] --> E[fault_ram_gen.py]
    E --> F[fault_ram.sv]
    C --> G[Verilator]
    F --> G
    G --> H["algo_engine.py (parse)"]
    H --> I[CampaignResult]
    I --> J["algo_reporting.py"]
    J --> K["report / matrix / diagnosis"]
```

`fault_primitives.py` is a declarative DSL: 15 of the 21 built-in fault types
are expressed as `(category, sensitize, effect)` triples, so a researcher can
register a *new* fault type from the shell without writing SystemVerilog.
`algo_engine.py` compiles the fault model plus either the march-algorithm
engine (driven by a `.alg` file) or a harness around your own controller FSM,
runs one golden pass and one pass per fault with Verilator, and reports
`DETECTED`/`ESCAPED` per fault.

Because this path never touches a real macro, its coverage numbers describe
the *algorithm's* detection power — useful for choosing or designing a march
algorithm before generating a wrapper for a specific memory.

## Why not one engine?

Three structural reasons, not preference:

1. **Different simulators, for structural reasons.** `fault_ram.sv` uses
   SystemVerilog queues/`foreach`/`final`, which Icarus doesn't support; the
   classic path's saboteur is deliberately simple, Icarus-friendly Verilog for
   cheap per-config iteration. Merging them means sacrificing one or the other.
2. **Different subjects under test.** The classic path always wraps a
   *specific* real memory. The algo-shell's fault model is a parameterized
   stand-in whose only job is a clean fault surface — it's not meant to
   resemble any macro's actual implementation.
3. **Different purposes**, asked at different times in a project's life.

## The multi-port invariant

Both subsystems' 2-port support is built on one non-negotiable rule: **a
2-port memory model shares exactly one underlying storage/fault-record array
across both ports.** Two independently instantiated single-port cores would
build and simulate fine, but silently defeat cross-port coupling-fault
testing — an aggressor write on port 1 disturbing a victim read on port 0 can
only be observed if both ports resolve against the *same* state. The engine's
`openram_shim_mp.sv` and the classic path's 1R1W/2RW templates are both built
around this rule; see {doc}`architecture` for the exact code that enforces
it.

## Where OpenRAM fits in

[OpenRAM](https://github.com/VLSIDA/OpenRAM) is the typical source of the
memory macro the classic path wraps. `autombist ram-synth` drives it directly
from an `openram.yml`. The classic path needs no shim — the generated wrapper
matches whatever pin names you declare. The algo-shell's `fault_ram.sv` has
its own engine-internal pin convention, so `openram_shim.sv` /
`openram_shim_mp.sv` adapt an OpenRAM-shaped interface onto it, letting a
researcher swap between "golden run against the real OpenRAM Verilog" and
"fault run against the shim-wrapped model" with the same testbench.

## Further reading

The full internal architecture doc — with the exact code excerpts backing
every claim above — lives at {doc}`architecture`.
