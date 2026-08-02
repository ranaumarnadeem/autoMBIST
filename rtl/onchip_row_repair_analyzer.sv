`timescale 1ns/1ps
// On-chip row-repair analyzer (Step E on-chip BIRA) -- a CAM-style registrar
// that tracks distinct failing ROW addresses streamed live by the controller
// (fail_valid/fail_addr, wired up on march_c/march_raw/march_x/mats_plus's
// FSMs, and on march_1r1w's single shared read-port compare) during a BIST
// pass, and freezes a row-repair signature into registered outputs on demand.
//
// This is the hardware equivalent of the software repair.bira.analyze() +
// repair.bisr.encode_row_repair() pipeline, SCOPED TO THE DEGENERATE ROW-ONLY
// CASE: with no spare columns, analyze()'s must-repair phase alone forces
// every distinct faulty row immediately (any positive column-degree exceeds a
// budget of 0) -- there is no combinatorial ambiguity left for a backtracking
// search to resolve, so a simple "first-come, lowest-free-slot" registrar is
// provably equivalent. This module does NOT implement (and does not need to
// implement) the general 2D branch-and-bound search in repair/bira.py.
//
// Output packing matches rtl/repair_remap_row.sv EXACTLY (no new convention):
// row_repair_en[i] enables spare i; faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH]
// is the logical address spare i replaces -- the same layout
// src/autombist/repair/bisr.py::encode_row_repair already produces for the
// tester-driven flow.
//
// Deliberate, documented properties (not bugs):
//   * Single-fail-per-cycle is assumed -- no arbitration for two simultaneous
//     fail_valid sources. Sound because every algo that reaches this module is
//     either inherently serial (march-c/march-raw/march-x/mats-plus: one
//     address in flight at a time) or has exactly one compare per cycle
//     despite being multi-port (march-1r1w: both ports share the same address
//     register, only the read port ever compares); this module is gated to
//     algo membership in generator.py's _SELFREPAIR_ALGOS for exactly that
//     reason. march-2rw's genuinely concurrent dual compare is excluded from
//     that set and would need real arbitration here.
//   * A PARTIAL repair is still applied when unrepairable is asserted: whatever
//     slots filled before the spare budget ran out remain latched into
//     row_repair_en/faulty_row_addr. This is a deliberate "fail-open-partially"
//     choice -- the caller's own re-verification pass is what actually proves
//     correctness -- not an oversight.
//   * Known defects ACCUMULATE for the life of the chip -- cleared only by
//     rst_n, never by re-running a self-repair pass. This is load-bearing, not
//     a simplification: once a repair is applied, the remap is ALWAYS active,
//     so any LATER analyze pass runs the march algorithm THROUGH the
//     already-repaired memory and (correctly, from its own point of view) sees
//     no fault at the repaired row. If a fresh pass cleared prior knowledge,
//     that "nothing wrong here" observation would latch an EMPTY signature and
//     silently erase a previously-correct repair, re-exposing a real,
//     already-fixed defect. Since a hard defect never "un-happens," only a
//     genuine reset (not just a re-trigger) is the correct point to forget one.
module onchip_row_repair_analyzer #(
    parameter integer ADDR_WIDTH     = 10,
    parameter integer NUM_SPARE_ROWS = 1
) (
    input  logic clk,
    input  logic rst_n,

    input  logic                  enable,       // registrar tracks fails only while high
    input  logic                  fail_valid,
    input  logic [ADDR_WIDTH-1:0] fail_addr,
    input  logic                  latch_result, // pulse: publishes accumulated state to outputs

    // --- Persisted-repair-signature load (Workstream 1.7), additive/optional ---
    // repair_load is a POR-time-only SINGLE-CYCLE pulse: restores a previously-
    // saved repair signature (e.g. from off-chip fuse/NVM storage) into BOTH the
    // internal live_valid/live_addr accumulator (so a later live analyze pass
    // correctly treats a persisted repair as already-known, per this module's
    // own "accumulate for the chip's lifetime" semantics above) and the
    // registered row_repair_en/faulty_row_addr outputs directly (active
    // immediately, no need to wait for a latch_result pulse). Ties to '0/unused
    // when the integrator never drives repair_load -- byte-identical to before
    // this was added.
    //
    // Contract, not hardware-enforced (adversarially reviewed -- both hazards
    // below are bounded by onchip_selfrepair_ctrl's independent verify-by-
    // re-execution: a violation makes repair ineffective or internally
    // inconsistent, but the system-level self_repair_fail status still comes
    // out correct, never a false pass):
    //   * Must be a single cycle, not held. Held for N cycles, it re-applies
    //     (or re-zeros, for an all-zero fuse bus) live_valid/live_addr every
    //     one of those cycles, discarding any live fail registration that
    //     lands during the hold -- repair becomes ineffective for as long as
    //     the hold lasts, e.g. a slow/glitchy fuse-read circuit.
    //   * Must not be pulsed the same cycle as a live fail registration OR a
    //     latch_result pulse. Vs. the live-fail block: repair_load's blanket
    //     write (placed textually after it below) wins for live_valid/
    //     live_addr AND row_repair_en/faulty_row_addr, consistently. Vs.
    //     latch_result (placed textually after repair_load below): latch_result
    //     wins row_repair_en/faulty_row_addr, but with THIS cycle's fresh
    //     repair_load write to live_valid/live_addr NOT YET visible to it (NBA
    //     reads see pre-edge values) -- so row_repair_en/faulty_row_addr can end
    //     up holding a STALE value while live_valid/live_addr hold the fresh
    //     one, a split-brain state that persists until a later repair_load or
    //     latch_result reconciles it. Neither collision can occur under the
    //     documented usage (repair_load only pre-self-repair-sequence;
    //     latch_result only fires mid-sequence, from S_ANALYZE_LATCH, which
    //     requires self_repair_busy=1) -- not interlocked in hardware.
    input  logic                                 repair_load,
    input  logic [NUM_SPARE_ROWS-1:0]            fuse_row_repair_en,
    input  logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] fuse_faulty_row_addr,
    output logic                                 repair_load_done,  // sticky, cleared only by rst_n

    output logic [NUM_SPARE_ROWS-1:0]            row_repair_en,
    output logic [NUM_SPARE_ROWS*ADDR_WIDTH-1:0] faulty_row_addr,
    output logic                                 unrepairable
);

    logic                  live_valid [0:NUM_SPARE_ROWS-1];
    logic [ADDR_WIDTH-1:0] live_addr  [0:NUM_SPARE_ROWS-1];
    logic                  live_unrepairable;

    logic   already_registered;
    logic   found_free_slot;
    integer free_slot;

    always_comb begin
        already_registered = 1'b0;
        found_free_slot    = 1'b0;
        free_slot          = 0;
        for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
            if (live_valid[i] && live_addr[i] == fail_addr) begin
                already_registered = 1'b1;
            end
            if (!found_free_slot && !live_valid[i]) begin
                found_free_slot = 1'b1;
                free_slot       = i;
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                live_valid[i] <= 1'b0;
                live_addr[i]  <= '0;
            end
            live_unrepairable <= 1'b0;
            row_repair_en     <= '0;
            faulty_row_addr   <= '0;
            unrepairable      <= 1'b0;
            repair_load_done  <= 1'b0;
        end else begin
            if (enable && fail_valid && !already_registered) begin
                if (found_free_slot) begin
                    live_valid[free_slot] <= 1'b1;
                    live_addr[free_slot]  <= fail_addr;
                end else begin
                    live_unrepairable <= 1'b1;  // sticky for the chip's lifetime
                end
            end

            // Placed AFTER the live-fail block above for defined last-write-wins
            // precedence if repair_load is (against the documented contract on
            // the port declaration above) ever pulsed the same cycle as a live
            // fail registration -- repair_load's blanket restore wins for that
            // cycle, consistently across live_valid/live_addr AND
            // row_repair_en/faulty_row_addr (see the port-declaration comment
            // for why the SAME claim does NOT hold against latch_result, placed
            // after this block). Does not touch live_unrepairable/unrepairable:
            // there is no fuse_unrepairable input (out of scope for this pass --
            // see module header); a persisted signature restores the repair map
            // only, not an analysis-time unrepairable verdict.
            if (repair_load) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    live_valid[i]    <= fuse_row_repair_en[i];
                    live_addr[i]     <= fuse_faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH];
                    row_repair_en[i] <= fuse_row_repair_en[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= fuse_faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH];
                end
                repair_load_done <= 1'b1;
            end

            if (latch_result) begin
                for (int i = 0; i < NUM_SPARE_ROWS; i++) begin
                    row_repair_en[i] <= live_valid[i];
                    faulty_row_addr[i*ADDR_WIDTH +: ADDR_WIDTH] <= live_addr[i];
                end
                unrepairable <= live_unrepairable;
            end
        end
    end

endmodule
