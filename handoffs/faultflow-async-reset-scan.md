# Handoff: FaultFlow needs async-reset/set scan support for autoMBIST controller grading

**Status:** blocking `autombist grade-controller`'s scan stuck-at ATPG step.
**Owner:** FaultFlow (this doc is read-only from autoMBIST's side — no FaultFlow files
are touched by this repo; nothing here has been pushed to FaultFlow).
**Established:** verified end-to-end against a real, built FaultFlow (Linux/WSL,
`ff.py` from FaultFlow's own venv) in an earlier session. Re-confirm before acting,
since FaultFlow may have moved on since.

## TL;DR

autoMBIST's controller-grading flow (`autombist grade-controller`) runs FaultFlow's
scan stuck-at ATPG against the synthesized MBIST controller, with the memory macro
blackboxed. The flow gets all the way through synthesis and FaultFlow's blackbox
recognition, then fails at scan insertion:

```
error: no eligible FF cells found to stitch; reasons: has_async_reset, has_async_set
```

**Cause:** autoMBIST's controller FSMs use asynchronous reset
(`always_ff @(posedge clk or negedge rst_n)`), which is standard practice for BIST
controllers. After Yosys `dfflibmap`, these become sky130 async-reset/set DFF cells
(`sky130_fd_sc_hd__dfrtp_*` / `__dfstp_*` / `__dfbbn_*`). FaultFlow's scan stitcher
currently disqualifies async-set/reset flip-flops outright, so with every controller
flop async-reset, there are zero eligible cells to scan and the campaign cannot run.

**Ask:** make async-set/reset flip-flops scannable (hold the async set/reset line
inactive during scan shift, standard DFT practice), rather than disqualifying them.

## What already works (verified on a real run, FaultFlow built, sky130)

1. Yosys synthesizes the collar with the memory replaced by a `(* blackbox *)` stub;
   the `u_sram` instance survives `flatten` and appears as a cell in the JSON netlist.
2. **FaultFlow correctly recognizes the blackbox cell** — log line:
   `OK: blackbox cell present in netlist`. The `[blackbox] instances = u_sram`
   config path and the pseudo-PI/PO boundary modeling work as documented; **no
   change needed here**.
3. `ff.py init --top <wrapper> -c <ofs>` succeeds:
   `initialized output/<wrapper>`.
4. The blocker is specifically `ff.py scan`, the very next step.

## Why this matters

Blackboxing the memory and scanning the controller is the whole point of the
integration: it lets FaultFlow answer "is the MBIST controller logic itself
manufacturable / testable?" — a question autoMBIST has no way to answer on its own
(it only validates the controller *functionally*, via cocotb). Without scan, the
controller's FSM state is unobservable/uncontrollable and combinational-only ATPG
cannot meaningfully grade it. This is a hard blocker on the whole `grade-controller`
feature actually producing a real coverage number (today it can only emit the bundle
and reach `ff.py init`, not complete `sim --scan`).

## Reproduction

Linux/WSL, FaultFlow built (`_faultflow_core` compiled), Yosys on PATH:

```bash
cd /path/to/openMBIST
autombist generate --config config.yml --out out          # clean wrapper, no saboteur
autombist grade-controller --out out --faultflow-repo /path/to/faultflow
# or, to inspect the bundle without running it:
autombist grade-controller --out out --no-run
cd out/<memory_name>/faultflow
FAULTFLOW_HOME=/path/to/faultflow bash run_faultflow.sh
```

The emitted `synth_collar.ys` and `<wrapper>.ofs` (in the `faultflow/` bundle
directory) are the exact inputs FaultFlow was run against. The synthesized
`<wrapper>.json` will show the controller's flip-flops mapped to async-reset sky130
cells if you want to inspect them directly.

## Likely code locations (from an earlier read of the FaultFlow tree; re-verify paths, they may have moved)

- Python: `faultflow/scan/stitch.py` — `stitch_scan_json()` and the ineligible-reason
  classifier that emits `has_async_reset` / `has_async_set`.
- C++ scan core: `src/core/scan/` — FF eligibility check and the scan
  load/launch/capture/unload protocol; `FFConfig` (set/reset fields) is likely in
  `src/core/common/types.hpp`.
- Cell map JSON: `cells/sky130/sky130_fd_sc_hd.json` — confirm scan-cell equivalents
  exist for the async-reset DFF cell types (sky130 HD does ship scan variants for
  these; the techmap step may need to target them).

## What "supporting" it means concretely

1. **Eligibility:** stop disqualifying FFs solely for `has_async_reset` /
   `has_async_set`; treat them as scannable.
2. **Scan-shift protocol:** hold the async set/reset line **inactive** during scan
   shift (the standard DFT pattern — async resets are gated off in shift mode so the
   FF behaves as a pure shift element) and model that correctly in the
   load/launch/capture/unload sequence.
3. **Cell mapping:** confirm the async-reset DFF cell types have scan-cell
   equivalents in the sky130 cell map / techmap step, and wire them in if not
   already.

## Alternative if this isn't prioritized soon (autoMBIST-side, not preferred)

autoMBIST could emit a **synchronous-reset** controller variant (flops would then be
scan-eligible with zero FaultFlow change). This is a larger RTL change on the
autoMBIST side, and async reset is the normal, correct choice for a BIST controller
(needs to reset independent of clock activity), so the FaultFlow-side fix is the
better long-term path. Flagging the alternative only so it's not a hard dependency.

## Status as of this doc

- autoMBIST's `grade-controller` bundle emission, Yosys synthesis, and blackbox
  recognition are all verified working.
- The `ff.py scan` step is the sole known blocker to a complete, real coverage number
  out of `autombist grade-controller`.
- No autoMBIST-side workaround has been applied (the controller RTL still uses async
  reset, as is correct); this doc exists so the fix, when made, lands in FaultFlow.
